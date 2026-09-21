"""Durable conversation messages and retryable outbound delivery records."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.models import Teacher
from app.family.models import FamilyConversation, FamilyMessage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _label(content: str, prefix: str) -> str:
    value = (content or "").strip()
    return value if value.startswith(prefix) else f"{prefix}{value}"


def get_or_create_conversation(
    db: Session, guardian_id: str, student_id: str, external_user_id: str
) -> FamilyConversation:
    query = select(FamilyConversation).where(
        FamilyConversation.guardian_id == guardian_id,
        FamilyConversation.student_id == student_id,
        FamilyConversation.channel == "wecom_customer",
        FamilyConversation.channel_conversation_id == external_user_id,
    )
    row = db.scalar(query)
    if row is not None:
        return row
    row = FamilyConversation(
        guardian_id=guardian_id, student_id=student_id,
        channel="wecom_customer", channel_conversation_id=external_user_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(query)
        if row is None:
            raise
    return row


def record_inbound_parent_message(
    db: Session, conversation_id: str, channel_message_id: str, content: str
) -> FamilyMessage:
    existing = db.scalar(select(FamilyMessage).where(
        FamilyMessage.channel_message_id == channel_message_id
    ))
    if existing is not None:
        return existing
    row = FamilyMessage(
        conversation_id=conversation_id, direction="inbound", sender_type="guardian",
        sender_id=db.get(FamilyConversation, conversation_id).guardian_id,
        channel_message_id=channel_message_id, content=content, status="completed",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(FamilyMessage).where(
            FamilyMessage.channel_message_id == channel_message_id
        ))
        if existing is None:
            raise
        return existing
    return row


def _record_outbound(
    db: Session, conversation_id: str, sender_type: str, sender_id: str | None,
    content: str, reply_to_message_id: str | None, image_id: str | None = None,
) -> FamilyMessage:
    row = FamilyMessage(
        conversation_id=conversation_id, direction="outbound", sender_type=sender_type,
        sender_id=sender_id, content=content, image_id=image_id,
        reply_to_message_id=reply_to_message_id, status="pending",
    )
    db.add(row)
    db.commit()
    return row


def record_bot_reply(
    db: Session, conversation_id: str, content: str, *,
    reply_to_message_id: str | None = None, image_id: str | None = None,
    status: str = "pending",
) -> FamilyMessage:
    if status != "pending":
        raise ValueError("新建的出站回复必须处于待发送状态")
    return _record_outbound(
        db, conversation_id, "bot", None,
        _label(content, "【机器人回复】"), reply_to_message_id, image_id,
    )


def record_bot_reply_batch(
    db: Session, conversation_id: str,
    actions: list[tuple[str, str | None]], *, reply_to_message_id: str,
    waiting_teacher: bool = False,
) -> list[FamilyMessage]:
    """Atomically persist every part of one inbound answer before delivery."""
    conversation = db.get(FamilyConversation, conversation_id)
    if conversation is None:
        raise ValueError("家庭会话不存在")
    existing = list(db.scalars(select(FamilyMessage).where(
        FamilyMessage.reply_to_message_id == reply_to_message_id,
        FamilyMessage.direction == "outbound",
        FamilyMessage.sender_type == "bot",
    )))
    rows: list[FamilyMessage] = []
    unmatched = list(existing)
    for content, image_id in actions:
        label = _label(content, "【机器人回复】")
        match = next((row for row in unmatched if row.content == label and row.image_id == image_id), None)
        if match is not None:
            unmatched.remove(match)
            continue
        rows.append(FamilyMessage(
            conversation_id=conversation_id, direction="outbound", sender_type="bot",
            content=label, image_id=image_id,
            reply_to_message_id=reply_to_message_id, status="pending",
        ))
    db.add_all(rows)
    if waiting_teacher:
        conversation.status = "waiting_teacher"
        conversation.updated_at = _now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return rows


def request_teacher_reply(
    db: Session, conversation_id: str, reason: str
) -> FamilyConversation:
    row = db.get(FamilyConversation, conversation_id)
    if row is None:
        raise ValueError("家庭会话不存在")
    row.status = "waiting_teacher"
    row.updated_at = _now()
    db.commit()
    return row


def record_teacher_reply(
    db: Session, conversation_id: str, teacher_id: str, content: str, *,
    reply_to_message_id: str | None = None, status: str = "pending",
) -> FamilyMessage:
    if status != "pending":
        raise ValueError("新建的出站回复必须处于待发送状态")
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    return _record_outbound(
        db, conversation_id, "teacher", teacher_id,
        _label(content, f"【老师：{teacher.name}】"), reply_to_message_id,
    )


def pending_replies_for_inbound(db: Session, channel_message_id: str) -> list[FamilyMessage]:
    inbound = db.scalar(select(FamilyMessage).where(
        FamilyMessage.channel_message_id == channel_message_id,
        FamilyMessage.direction == "inbound",
    ))
    if inbound is None:
        return []
    return list(db.scalars(select(FamilyMessage).where(
        FamilyMessage.reply_to_message_id == inbound.message_id,
        FamilyMessage.direction == "outbound",
        FamilyMessage.status.in_(("pending", "failed")),
    ).order_by(FamilyMessage.created_at, FamilyMessage.message_id)))


def outbound_for_inbound(db: Session, inbound_message_id: str) -> list[FamilyMessage]:
    return list(db.scalars(select(FamilyMessage).where(
        FamilyMessage.reply_to_message_id == inbound_message_id,
        FamilyMessage.direction == "outbound",
    ).order_by(FamilyMessage.created_at, FamilyMessage.message_id)))


def mark_message_sent(db: Session, message_id: str, channel_message_id: str) -> FamilyMessage:
    row = db.get(FamilyMessage, message_id)
    if row is None or row.direction != "outbound":
        raise ValueError("出站消息不存在")
    if not isinstance(channel_message_id, str) or not channel_message_id.strip():
        raise ValueError("渠道未返回消息编号")
    row.status = "completed"
    row.failure_reason = None
    row.channel_message_id = channel_message_id.strip()
    row.sent_at = _now()
    db.commit()
    return row


def mark_message_failed(db: Session, message_id: str, reason: str) -> FamilyMessage:
    row = db.get(FamilyMessage, message_id)
    if row is None or row.direction != "outbound":
        raise ValueError("出站消息不存在")
    row.status = "failed"
    row.failure_reason = (reason or "渠道发送失败")[:200]
    row.sent_at = None
    db.commit()
    return row
