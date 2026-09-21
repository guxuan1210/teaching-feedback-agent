import asyncio
from datetime import date

import pytest

from app.catalog.models import Student, Teacher
from app.family.conversations import record_inbound_parent_message
from app.family.models import (
    FamilyConversation,
    Guardian,
    GuardianChannelBinding,
    StudentGuardian,
)
from app.family.relationships import assign_teacher, revoke_teacher_assignment
from app.wecom.config import WecomConfig
from app.wecom.gateway import WecomGateway
from app.wecom.family_handler import process_family_reply_command
from app.wecom.handler import process_text
from app.wecom.models import TeacherWecomBinding, WecomInboundMessage


class Customer:
    def __init__(self, fails=False):
        self.texts = []
        self.fails = fails

    def send_text(self, external_user_id, content):
        if self.fails:
            raise RuntimeError("upstream down")
        self.texts.append((external_user_id, content))
        return "OUT-1"


@pytest.fixture
def pending_conversation(db_session, teacher, student):
    assignment = assign_teacher(db_session, student.student_id, teacher.teacher_id, "primary", date(2026, 1, 1))
    db_session.add(TeacherWecomBinding(wecom_user_id="WX-TEACHER", teacher_id=teacher.teacher_id))
    guardian = Guardian(guardian_id="G1", name="李女士", relationship_type="mother", status="active")
    db_session.add(guardian)
    db_session.commit()
    db_session.add_all([
        StudentGuardian(student_id=student.student_id, guardian_id=guardian.guardian_id, status="active"),
        GuardianChannelBinding(channel="wecom_customer", external_user_id="EXT-PARENT", guardian_id=guardian.guardian_id, active_student_id=student.student_id, status="active"),
    ])
    conversation = FamilyConversation(
        conversation_id="FAM-1", guardian_id=guardian.guardian_id,
        student_id=student.student_id, assigned_teacher_id=teacher.teacher_id,
        channel="wecom_customer", channel_conversation_id="EXT-PARENT",
        status="waiting_teacher",
    )
    db_session.add(conversation)
    db_session.commit()
    inbound = record_inbound_parent_message(
        db_session, conversation.conversation_id, "PARENT-1", "孩子这周作业需要怎么准备？"
    )
    return conversation, inbound, assignment


def test_parent_question_is_pushed_to_primary_teacher(engine, teacher, student, pending_conversation):
    from app.core.database import build_session_factory

    conversation, _inbound, _assignment = pending_conversation

    class Bot:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, user_id, payload):
            self.sent_messages.append({"user_id": user_id, **payload})

    bot = Bot()
    gateway = WecomGateway(
        config=WecomConfig(True, "bot", "secret", None),
        session_factory=build_session_factory(engine), chat_provider=None,
        binding_secret="secret", client_factory=lambda _config: bot,
    )
    gateway.client = bot

    assert asyncio.run(gateway.notify_teacher(conversation.conversation_id)) is True

    payload = bot.sent_messages[-1]
    content = payload["markdown"]["content"]
    assert payload["user_id"] == "WX-TEACHER"
    assert "家长请求老师回复" in content and "FAM-1" in content
    assert "测试学生" in content and "母亲" in content
    assert "孩子这周作业需要怎么准备？" in content
    assert "EXT-PARENT" not in content


def test_revoked_parent_channel_binding_does_not_trigger_teacher_notification(
    engine, pending_conversation, db_session
):
    from app.core.database import build_session_factory

    conversation, _inbound, _assignment = pending_conversation
    parent_binding = db_session.query(GuardianChannelBinding).filter_by(
        external_user_id="EXT-PARENT"
    ).one()
    parent_binding.status = "revoked"
    db_session.commit()

    class Bot:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, user_id, payload):
            self.sent_messages.append((user_id, payload))

    bot = Bot()
    gateway = WecomGateway(
        config=WecomConfig(True, "bot", "secret", None),
        session_factory=build_session_factory(engine), chat_provider=None,
        binding_secret="secret", client_factory=lambda _config: bot,
    )
    gateway.client = bot

    assert asyncio.run(gateway.notify_teacher(conversation.conversation_id)) is False
    assert bot.sent_messages == []


def test_teacher_reply_is_delivered_with_server_generated_label(db_session, pending_conversation):
    conversation, _inbound, _assignment = pending_conversation
    customer = Customer()

    event = process_family_reply_command(
        db_session, "WX-TEACHER", "回复 FAM-1 已了解，谢谢反馈", customer
    )

    from app.family.models import FamilyMessage
    sent = db_session.query(FamilyMessage).filter_by(sender_type="teacher", direction="outbound").one()
    assert customer.texts[-1] == ("EXT-PARENT", "【老师：测试教师】已了解，谢谢反馈")
    assert sent.status == "completed" and sent.sent_at
    assert event.content == "已回复家长。"


def test_revoked_teacher_cannot_reply(db_session, pending_conversation):
    _conversation, _inbound, assignment = pending_conversation
    from datetime import timedelta
    revoke_teacher_assignment(db_session, assignment.assignment_id, date.today() - timedelta(days=1))
    customer = Customer()

    event = process_family_reply_command(
        db_session, "WX-TEACHER", "回复 FAM-1 已了解", customer
    )

    assert event.content == "你已不再负责该学生，无法回复。"
    assert customer.texts == []


@pytest.mark.parametrize("revoked_relation", ["channel", "student_guardian"])
def test_revoked_parent_access_cannot_receive_teacher_reply(
    db_session, pending_conversation, revoked_relation
):
    if revoked_relation == "channel":
        binding = db_session.query(GuardianChannelBinding).filter_by(
            external_user_id="EXT-PARENT"
        ).one()
        binding.status = "revoked"
    else:
        relation = db_session.query(StudentGuardian).filter_by(
            student_id="S1", guardian_id="G1"
        ).one()
        relation.status = "revoked"
    db_session.commit()
    customer = Customer()

    event = process_family_reply_command(
        db_session, "WX-TEACHER", "回复 FAM-1 已了解", customer
    )

    from app.family.models import FamilyMessage
    assert event.content == "家长绑定已失效，无法发送回复。"
    assert customer.texts == []
    assert db_session.query(FamilyMessage).filter_by(
        conversation_id="FAM-1", sender_type="teacher", direction="outbound"
    ).count() == 0


def test_customer_delivery_failure_is_recorded(db_session, pending_conversation):
    event = process_family_reply_command(
        db_session, "WX-TEACHER", "回复 FAM-1 已了解", Customer(fails=True)
    )

    from app.family.models import FamilyMessage
    failed = db_session.query(FamilyMessage).filter_by(sender_type="teacher", direction="outbound").one()
    assert event.content == "家长消息发送失败，请稍后重试。"
    assert failed.status == "failed"
    assert failed.failure_reason == "渠道发送失败：RuntimeError"


def test_family_reply_command_is_handled_before_teaching_scope_and_not_saved_as_chat(db_session, teacher, pending_conversation):
    customer = Customer()

    events = list(process_text(
        db_session, None, "secret", "WECHAT-CMD-1", "WX-TEACHER",
        "回复 FAM-1 已了解", customer_client=customer,
    ))

    from app.chat.models import ChatMessage
    assert events[-1].content == "已回复家长。"
    assert db_session.query(ChatMessage).count() == 0
    assert db_session.get(WecomInboundMessage, "WECHAT-CMD-1").status == "completed"
