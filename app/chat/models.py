"""Chat tables: a scoped conversation, its messages, and per-message sources."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatConversation(Base):
    __tablename__ = "chat_conversation"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    class_id: Mapped[str] = mapped_column(
        String, ForeignKey("class.class_id"), nullable=False
    )
    student_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("student.student_id")
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    date_from: Mapped[str] = mapped_column(String, nullable=False)
    date_to: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    last_message_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("scope_type IN ('student','class')", name="ck_chat_scope_type"),
        CheckConstraint("date_to >= date_from", name="ck_chat_dates"),
        CheckConstraint("status IN ('active','archived')", name="ck_chat_status"),
        CheckConstraint(
            "(scope_type = 'student' AND student_id IS NOT NULL) "
            "OR (scope_type = 'class' AND student_id IS NULL)",
            name="ck_chat_student_scope",
        ),
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("chat_conversation.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    model: Mapped[str | None] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    context_date_from: Mapped[str | None] = mapped_column(String)
    context_date_to: Mapped[str | None] = mapped_column(String)
    context_snapshot: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)

    conversation: Mapped["ChatConversation"] = relationship(back_populates="messages")
    sources: Mapped[list["ChatMessageSource"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_chat_message_role"),
        CheckConstraint(
            "status IN ('completed','failed')", name="ck_chat_message_status"
        ),
        CheckConstraint(
            "channel IS NULL OR channel IN ('web','wecom')",
            name="ck_chat_message_channel",
        ),
    )


class TeacherChatState(Base):
    __tablename__ = "teacher_chat_state"

    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="CASCADE"), primary_key=True
    )
    current_conversation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("chat_conversation.conversation_id", ondelete="SET NULL")
    )
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)


class ChatMessageSource(Base):
    __tablename__ = "chat_message_source"

    message_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("chat_message.message_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_type: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    cited: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    message: Mapped["ChatMessage"] = relationship(back_populates="sources")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('daily','special','weekly_report')",
            name="ck_chat_source_type",
        ),
        CheckConstraint("cited IN (0,1)", name="ck_chat_source_cited"),
    )
