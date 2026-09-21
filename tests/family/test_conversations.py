from __future__ import annotations

from app.family import conversations
from app.family.models import FamilyMessage


def test_outbound_reply_is_durably_linked_before_delivery(db_session, student, teacher):
    from app.family.models import FamilyConversation, Guardian

    guardian = Guardian(name="家长", relationship_type="other")
    db_session.add(guardian)
    db_session.flush()
    conversation = conversations.get_or_create_conversation(
        db_session, guardian.guardian_id, student.student_id, "EXT1"
    )
    inbound = conversations.record_inbound_parent_message(
        db_session, conversation.conversation_id, "IN-1", "最近情况如何"
    )
    outbound = conversations.record_bot_reply(
        db_session, conversation.conversation_id, "答复", reply_to_message_id=inbound.message_id
    )

    assert inbound.status == "completed"
    assert outbound.status == "pending"
    assert outbound.content == "【机器人回复】答复"
    assert outbound.reply_to_message_id == inbound.message_id
    assert conversations.pending_replies_for_inbound(db_session, "IN-1") == [outbound]


def test_failed_outbound_remains_retryable(db_session, student, teacher):
    from app.family.models import FamilyConversation, Guardian

    guardian = Guardian(name="家长", relationship_type="other")
    db_session.add(guardian)
    db_session.flush()
    conversation = conversations.get_or_create_conversation(
        db_session, guardian.guardian_id, student.student_id, "EXT1"
    )
    inbound = conversations.record_inbound_parent_message(
        db_session, conversation.conversation_id, "IN-2", "问题"
    )
    outbound = conversations.record_bot_reply(
        db_session, conversation.conversation_id, "答复", reply_to_message_id=inbound.message_id
    )
    conversations.mark_message_failed(db_session, outbound.message_id, "暂时无法发送")

    assert outbound.status == "failed"
    assert conversations.pending_replies_for_inbound(db_session, "IN-2") == [outbound]
    conversations.mark_message_sent(db_session, outbound.message_id, "OUT-2")
    assert outbound.status == "completed"
    assert outbound.channel_message_id == "OUT-2"


def test_success_requires_channel_message_id_for_idempotency(db_session, student, teacher):
    from app.family.models import FamilyConversation, Guardian
    import pytest

    guardian = Guardian(name="家长", relationship_type="other")
    db_session.add(guardian)
    db_session.flush()
    conversation = conversations.get_or_create_conversation(
        db_session, guardian.guardian_id, student.student_id, "EXT1"
    )
    inbound = conversations.record_inbound_parent_message(
        db_session, conversation.conversation_id, "IN-3", "问题"
    )
    outbound = conversations.record_bot_reply(
        db_session, conversation.conversation_id, "答复", reply_to_message_id=inbound.message_id
    )

    with pytest.raises(ValueError, match="渠道未返回消息编号"):
        conversations.mark_message_sent(db_session, outbound.message_id, "")
