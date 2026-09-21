"""Teacher-side commands for audited family conversations."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Teacher
from app.family import conversations
from app.family.models import (
    FamilyConversation,
    FamilyMessage,
    Guardian,
    GuardianChannelBinding,
    StudentGuardian,
)
from app.family.permissions import teacher_can_access_student
from app.wecom.models import TeacherWecomBinding


_REPLY = re.compile(r"^回复\s+(\S+)\s+(.+)$", re.DOTALL)


def is_family_reply_command(text: str) -> bool:
    """Reserve messages beginning with 回复 for the family command handler."""
    return (text or "").strip().startswith("回复")


def process_family_reply_command(
    db: Session,
    wecom_user_id: str,
    text: str,
    customer_client,
):
    """Validate, persist, and deliver a teacher reply to one parent conversation.

    Returns ``None`` only when the input is not a reserved family command.
    """
    from app.wecom.handler import BotEvent

    content = (text or "").strip()
    if not is_family_reply_command(content):
        return None
    match = _REPLY.fullmatch(content)
    if match is None:
        return BotEvent("failed", "格式：回复 会话编号 回复内容")

    binding = db.get(TeacherWecomBinding, wecom_user_id)
    if binding is None:
        return BotEvent("failed", "请先绑定老师账号，再回复家长。")
    teacher = db.get(Teacher, binding.teacher_id)
    if teacher is None or teacher.status != "active":
        return BotEvent("failed", "教师账号已停用，无法回复家长。")

    conversation_id, reply_text = match.groups()
    family_conversation = db.get(FamilyConversation, conversation_id)
    if family_conversation is None or family_conversation.channel != "wecom_customer":
        return BotEvent("failed", "未找到该家长会话，请核对会话编号。")
    if family_conversation.status != "waiting_teacher":
        return BotEvent("failed", "该会话当前无需老师回复。")
    if not teacher_can_access_student(db, teacher.teacher_id, family_conversation.student_id):
        return BotEvent("failed", "你已不再负责该学生，无法回复。")

    guardian = db.get(Guardian, family_conversation.guardian_id)
    relation = db.scalar(select(StudentGuardian.relation_id).where(
        StudentGuardian.student_id == family_conversation.student_id,
        StudentGuardian.guardian_id == family_conversation.guardian_id,
        StudentGuardian.status == "active",
    ))
    channel_binding = db.scalar(select(GuardianChannelBinding).where(
        GuardianChannelBinding.channel == "wecom_customer",
        GuardianChannelBinding.external_user_id == family_conversation.channel_conversation_id,
        GuardianChannelBinding.guardian_id == family_conversation.guardian_id,
        GuardianChannelBinding.status == "active",
    ))
    if guardian is None or guardian.status != "active" or relation is None or channel_binding is None:
        return BotEvent("failed", "家长绑定已失效，无法发送回复。")

    latest_parent_message = db.scalar(select(FamilyMessage).where(
        FamilyMessage.conversation_id == conversation_id,
        FamilyMessage.direction == "inbound",
        FamilyMessage.sender_type == "guardian",
    ).order_by(FamilyMessage.created_at.desc(), FamilyMessage.message_id.desc()).limit(1))
    outbound = conversations.record_teacher_reply(
        db, conversation_id, teacher.teacher_id, reply_text,
        reply_to_message_id=latest_parent_message.message_id if latest_parent_message else None,
    )
    if customer_client is None:
        conversations.mark_message_failed(db, outbound.message_id, "微信客服客户端未配置")
        return BotEvent("failed", "家长消息发送失败，请稍后重试。")
    try:
        channel_message_id = customer_client.send_text(
            family_conversation.channel_conversation_id, outbound.content
        )
        conversations.mark_message_sent(db, outbound.message_id, channel_message_id)
        family_conversation.status = "active"
        db.commit()
    except Exception as exc:  # upstream text and credentials are never propagated
        conversations.mark_message_failed(
            db, outbound.message_id, f"渠道发送失败：{type(exc).__name__}"
        )
        return BotEvent("failed", "家长消息发送失败，请稍后重试。")
    return BotEvent("completed", "已回复家长。")
