from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeacherWecomBinding(Base):
    __tablename__ = "teacher_wecom_binding"

    wecom_user_id: Mapped[str] = mapped_column(String, primary_key=True)
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="CASCADE"), unique=True
    )
    bound_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)


class WecomBindingCode(Base):
    __tablename__ = "wecom_binding_code"

    code_id: Mapped[str] = mapped_column(String, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)


class WecomChatState(Base):
    __tablename__ = "wecom_chat_state"

    wecom_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("teacher_wecom_binding.wecom_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("chat_conversation.conversation_id", ondelete="SET NULL")
    )
    scope_type: Mapped[str | None] = mapped_column(String)
    class_id: Mapped[str | None] = mapped_column(String, ForeignKey("class.class_id"))
    student_id: Mapped[str | None] = mapped_column(String, ForeignKey("student.student_id"))
    date_from: Mapped[str | None] = mapped_column(String)
    date_to: Mapped[str | None] = mapped_column(String)
    pending_scope_json: Mapped[str | None] = mapped_column(Text)
    pending_question: Mapped[str | None] = mapped_column(Text)
    last_failed_message_id: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)


class WecomInboundMessage(Base):
    __tablename__ = "wecom_inbound_message"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    wecom_user_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    completed_at: Mapped[str | None] = mapped_column(String)
