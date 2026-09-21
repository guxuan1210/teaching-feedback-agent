from __future__ import annotations

from app.family.models import FamilyConversation, FamilyMessage, Guardian, StudentGuardian
from app.family.conversations import record_inbound_parent_message
from app.family.parent_assistant import answer_parent_question


class Provider:
    def __init__(self, answer="近期进展不错"):
        self.answer_text = answer
        self.context = None

    def answer(self, prompt, context):
        self.context = context
        if isinstance(self.answer_text, Exception):
            raise self.answer_text
        return self.answer_text


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
