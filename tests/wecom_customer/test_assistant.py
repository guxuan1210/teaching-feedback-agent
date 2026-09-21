from __future__ import annotations

from app.family.models import FamilyConversation, FamilyMessage, Guardian, StudentGuardian
from app.family.conversations import record_inbound_parent_message
from app.family.parent_assistant import answer_parent_question
from app.wecom_customer.handler import process_parent_text


class Provider:
    def __init__(self, answer="近期进展不错"):
        self.answer_text = answer
        self.context = None

    def answer(self, prompt, context):
        self.context = context
        if isinstance(self.answer_text, Exception):
            raise self.answer_text
        return self.answer_text


class RecordingCustomer:
    def __init__(self):
        self.sent = []

    def send_text(self, external_user_id, content):
        self.sent.append((external_user_id, content))
        return f"OUT-{len(self.sent)}"


def _conversation(db_session, student):
    guardian = Guardian(name="家长", relationship_type="mother")
    db_session.add(guardian)
    db_session.flush()
    db_session.add(StudentGuardian(student_id=student.student_id, guardian_id=guardian.guardian_id))
    db_session.flush()
    conversation = FamilyConversation(
        guardian_id=guardian.guardian_id, student_id=student.student_id,
        channel="wecom_customer", channel_conversation_id="EXT1",
    )
    db_session.add(conversation)
    db_session.commit()
    return guardian, conversation


def test_bot_reply_is_labeled_and_uses_only_authorized_student_context(db_session, student):
    provider = Provider()
    guardian, _conversation_row = _conversation(db_session, student)

    result = answer_parent_question(
        db_session, provider, guardian_id=guardian.guardian_id,
        student_id=student.student_id, text="最近状态如何",
    )

    assert result.outbound_text.startswith("【机器人回复】")
    assert result.message.sender_type == "bot"
    assert all(item.get("student_id") in (None, student.student_id) for item in provider.context)


def test_model_failure_is_preserved_and_routes_to_teacher(db_session, student):
    provider = Provider(RuntimeError("provider unavailable"))
    guardian, conversation = _conversation(db_session, student)

    result = answer_parent_question(
        db_session, provider, guardian_id=guardian.guardian_id,
        student_id=student.student_id, text="最近状态如何",
    )

    assert result.route_to_teacher is True
    assert result.inbound_message.status == "completed"
    assert result.outbound_text == "【机器人回复】我已记录问题并转交负责老师。"
    assert conversation.status == "waiting_teacher"


def test_reply_replay_retries_persisted_outbox_after_channel_failure(db_session, student, teacher):
    from app.family.invitations import create_guardian_invitation
    from app.family.models import GuardianChannelBinding
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND1", text=code)
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND2", text="母亲")
    binding = db_session.query(GuardianChannelBinding).one()

    class FlakyCustomer:
        def __init__(self):
            self.attempts = []
        def send_text(self, external_user_id, content):
            self.attempts.append(content)
            if len(self.attempts) == 1:
                raise RuntimeError("temporary API outage")
            return "OUT-1"

    customer = FlakyCustomer()
    first = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q1", text="你好",
    )
    outbound = db_session.query(FamilyMessage).filter_by(
        direction="outbound", reply_to_message_id=db_session.query(FamilyMessage.message_id)
        .filter_by(channel_message_id="Q1").scalar(),
    ).one()
    assert customer.attempts == [outbound.content], (outbound.reply_to_message_id, outbound.status)
    assert outbound.status == "failed"
    assert outbound.content.startswith("【机器人回复】")
    assert customer.attempts == [outbound.content]

    replay = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q1", text="你好",
    )
    assert customer.attempts == [outbound.content, outbound.content]
    assert outbound.status == "completed"
    assert outbound.channel_message_id == "OUT-1"
    assert replay[0].content == first[0].content == outbound.content


def test_callback_replay_recovers_inbound_left_without_outbox(db_session, student, teacher):
    from app.family.invitations import create_guardian_invitation
    from app.family.models import GuardianChannelBinding
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND1", text=code)
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND2", text="母亲")
    binding = db_session.query(GuardianChannelBinding).one()
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    record_inbound_parent_message(db_session, conversation.conversation_id, "Q-CRASH", "你好")

    class Customer:
        def __init__(self): self.sent = []
        def send_text(self, external_user_id, content): self.sent.append(content); return "OUT-2"

    customer = Customer()
    result = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-CRASH", text="你好",
    )
    assert len(customer.sent) == 1
    assert customer.sent[0].startswith("【机器人回复】")
    assert result[0].content == customer.sent[0]


def test_bound_handler_persists_provider_answer_under_channel_message_id(db_session, student, teacher):
    from app.family.invitations import create_guardian_invitation
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND1", text=code)
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND2", text="母亲")
    provider = Provider("孩子最近按时完成练习")

    result = process_parent_text(
        db_session, object(), secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-MODEL", text="能聊聊孩子最近的学习吗？", assistant_provider=provider,
    )

    inbound = db_session.query(FamilyMessage).filter_by(channel_message_id="Q-MODEL").one()
    outbound = db_session.query(FamilyMessage).filter_by(
        reply_to_message_id=inbound.message_id, direction="outbound"
    ).one()
    assert inbound.status == "completed"
    assert outbound.content.startswith("【机器人回复】孩子最近按时完成练习")
    assert result[0].content == outbound.content


def test_replay_recovers_free_form_answer_after_inbound_commit(db_session, student, teacher):
    from app.family.conversations import record_inbound_parent_message
    from app.family.invitations import create_guardian_invitation
    from app.family.models import GuardianChannelBinding
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND1", text=code)
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND2", text="母亲")
    binding = db_session.query(GuardianChannelBinding).one()
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    record_inbound_parent_message(
        db_session, conversation.conversation_id, "Q-LLM-CRASH", "能聊聊最近的学习吗？"
    )

    class CountingProvider:
        def __init__(self): self.calls = 0
        def answer(self, prompt, context):
            self.calls += 1
            return "孩子最近稳步进步。"

    class Customer:
        def __init__(self): self.sent = []
        def send_text(self, external_user_id, content):
            self.sent.append(content)
            return f"OUT-{len(self.sent)}"

    provider, customer = CountingProvider(), Customer()
    args = dict(
        secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-LLM-CRASH", text="能聊聊最近的学习吗？",
        assistant_provider=provider,
    )
    first = process_parent_text(db_session, customer, **args)
    second = process_parent_text(db_session, customer, **args)

    inbound = db_session.query(FamilyMessage).filter_by(channel_message_id="Q-LLM-CRASH").one()
    outbound = db_session.query(FamilyMessage).filter_by(
        reply_to_message_id=inbound.message_id, direction="outbound"
    ).all()
    assert provider.calls == 1
    assert len(outbound) == 1
    assert outbound[0].content == "【机器人回复】孩子最近稳步进步。"
    assert outbound[0].status == "completed"
    assert customer.sent == [outbound[0].content]
    assert first[0].content == outbound[0].content
    assert second[-1].content == "【机器人回复】这条消息已处理。"


def test_replayed_manual_handoff_creates_durable_fallback(db_session, student, teacher):
    from app.family.conversations import record_inbound_parent_message
    from app.family.invitations import create_guardian_invitation
    from app.family.models import GuardianChannelBinding
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND1", text=code)
    process_parent_text(db_session, object(), secret_key="test-invitation-secret",
                        external_user_id="EXT1", message_id="BIND2", text="母亲")
    binding = db_session.query(GuardianChannelBinding).one()
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    record_inbound_parent_message(
        db_session, conversation.conversation_id, "Q-HANDOFF-CRASH", "我想投诉这次服务"
    )

    class Provider:
        def answer(self, prompt, context): raise AssertionError("manual handoff skips the model")
    class Customer:
        def send_text(self, external_user_id, content): return "OUT-HANDOFF"

    replies = process_parent_text(
        db_session, Customer(), secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-HANDOFF-CRASH", text="我想投诉这次服务", assistant_provider=Provider(),
    )
    inbound = db_session.query(FamilyMessage).filter_by(channel_message_id="Q-HANDOFF-CRASH").one()
    outbound = db_session.query(FamilyMessage).filter_by(
        reply_to_message_id=inbound.message_id, direction="outbound"
    ).one()
    assert outbound.content == "【机器人回复】我已记录问题并转交负责老师。"
    assert outbound.status == "completed"
    assert conversation.status == "waiting_teacher"
    assert replies[0].content == outbound.content


def _bind_external_parent(db_session, student, teacher, external_user_id="EXT1"):
    from app.family.invitations import create_guardian_invitation
    from app.wecom_customer.handler import process_parent_text

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key="test-invitation-secret"
    )
    process_parent_text(
        db_session, object(), secret_key="test-invitation-secret",
        external_user_id=external_user_id, message_id=f"{external_user_id}-BIND1", text=code,
    )
    process_parent_text(
        db_session, object(), secret_key="test-invitation-secret",
        external_user_id=external_user_id, message_id=f"{external_user_id}-BIND2", text="母亲",
    )
    from app.family.models import GuardianChannelBinding
    return db_session.query(GuardianChannelBinding).filter_by(
        external_user_id=external_user_id
    ).one()


def _add_second_child(db_session, binding):
    from app.catalog.models import Student
    child = Student(
        student_id="S2", name="另一个孩子", grade="三年级",
        current_stage="三阶", status="active",
    )
    db_session.add(child)
    db_session.flush()
    db_session.add(StudentGuardian(student_id=child.student_id, guardian_id=binding.guardian_id))
    binding.active_student_id = child.student_id
    db_session.commit()
    return child


def test_replay_after_child_switch_uses_original_inbound_student(db_session, student, teacher):
    from app.family.conversations import record_inbound_parent_message
    binding = _bind_external_parent(db_session, student, teacher)
    _add_second_child(db_session, binding)
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    record_inbound_parent_message(
        db_session, conversation.conversation_id, "Q-OLD-CHILD", "这条旧消息属于原孩子"
    )

    class Provider:
        def __init__(self): self.context = None
        def answer(self, prompt, context): self.context = context; return "原孩子的回复"
    class Customer:
        def __init__(self): self.sent = []
        def send_text(self, external_user_id, content): self.sent.append((external_user_id, content)); return "OUT-OLD"

    provider, customer = Provider(), Customer()
    replies = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-OLD-CHILD", text="这条旧消息属于原孩子", assistant_provider=provider,
    )
    assert provider.context[0]["student_id"] == student.student_id
    assert replies[0].content == "【机器人回复】原孩子的回复"
    assert customer.sent == [("EXT1", replies[0].content)]


def test_duplicate_from_different_external_user_is_never_delivered(db_session, student, teacher):
    from app.family.conversations import record_bot_reply, record_inbound_parent_message
    binding = _bind_external_parent(db_session, student, teacher, "EXT1")
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    inbound = record_inbound_parent_message(
        db_session, conversation.conversation_id, "Q-FOREIGN-REPLAY", "private question"
    )
    record_bot_reply(
        db_session, conversation.conversation_id, "private answer",
        reply_to_message_id=inbound.message_id,
    )

    class Customer:
        def __init__(self): self.sent = []
        def send_text(self, external_user_id, content): self.sent.append((external_user_id, content)); return "OUT-X"

    customer = Customer()
    result = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT2",
        message_id="Q-FOREIGN-REPLAY", text="forged replay",
        assistant_provider=Provider("should not run"),
    )
    assert result == []
    assert customer.sent == []


def test_replay_refuses_original_student_after_relationship_revocation(db_session, student, teacher):
    from app.family.conversations import record_inbound_parent_message
    binding = _bind_external_parent(db_session, student, teacher)
    _add_second_child(db_session, binding)
    relation = db_session.query(StudentGuardian).filter_by(
        student_id=student.student_id, guardian_id=binding.guardian_id
    ).one()
    relation.status = "revoked"
    db_session.commit()
    conversation = db_session.query(FamilyConversation).filter_by(
        guardian_id=binding.guardian_id, student_id=student.student_id
    ).one()
    record_inbound_parent_message(
        db_session, conversation.conversation_id, "Q-REVOKED-REPLAY", "private old question"
    )

    class Provider:
        def __init__(self): self.calls = 0
        def answer(self, prompt, context): self.calls += 1; return "must not be sent"
    class Customer:
        def __init__(self): self.sent = []
        def send_text(self, external_user_id, content): self.sent.append(content); return "OUT-R"

    provider, customer = Provider(), Customer()
    process_parent_text(
        db_session, customer, secret_key="test-invitation-secret", external_user_id="EXT1",
        message_id="Q-REVOKED-REPLAY", text="private old question",
        assistant_provider=provider,
    )
    assert provider.calls == 0
    assert customer.sent == []
    assert db_session.query(FamilyMessage).filter_by(
        reply_to_message_id=db_session.query(FamilyMessage.message_id)
        .filter_by(channel_message_id="Q-REVOKED-REPLAY").scalar(),
        direction="outbound",
    ).count() == 0


def test_prebinding_prompts_are_sent_to_customer_without_storing_invite_code(
    db_session, student, teacher
):
    from app.family.invitations import create_guardian_invitation

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id,
        secret_key="test-invitation-secret",
    )
    customer = RecordingCustomer()
    process_parent_text(
        db_session, customer, secret_key="test-invitation-secret",
        external_user_id="EXT-PROMPT", message_id="P1", text=code,
    )
    process_parent_text(
        db_session, customer, secret_key="test-invitation-secret",
        external_user_id="EXT-PROMPT", message_id="P2", text="母亲",
    )
    assert customer.sent[0][1].startswith("【机器人回复】请选择身份")
    assert customer.sent[1][1].startswith("【机器人回复】绑定成功")
    assert not db_session.query(FamilyMessage).filter(
        FamilyMessage.content == code
    ).count()


def test_unbound_prompt_is_sent_and_revoked_pending_does_not_raise(
    db_session, student, teacher
):
    from app.family.invitations import create_guardian_invitation
    from app.family.models import GuardianChannelBinding

    teacher.role = "管理员"
    db_session.commit()
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id,
        secret_key="test-invitation-secret",
    )
    customer = RecordingCustomer()
    process_parent_text(
        db_session, customer, secret_key="test-invitation-secret",
        external_user_id="EXT-REVOKE", message_id="R1", text="你好",
    )
    assert customer.sent[-1][1].startswith("【机器人回复】请先发送管理员")
    process_parent_text(
        db_session, customer, secret_key="test-invitation-secret",
        external_user_id="EXT-REVOKE", message_id="R2", text=code,
    )
    binding = db_session.query(GuardianChannelBinding).filter_by(
        external_user_id="EXT-REVOKE"
    ).one()
    binding.status = "revoked"
    db_session.commit()
    result = process_parent_text(
        db_session, customer, secret_key="test-invitation-secret",
        external_user_id="EXT-REVOKE", message_id="R3", text="母亲",
    )
    assert result[0].content.startswith("【机器人回复】请先发送管理员")
    assert customer.sent[-1][1] == result[0].content
