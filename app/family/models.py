"""Family relationships, student media, and guardian communication tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Guardian(Base):
    __tablename__ = "guardian"

    guardian_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "relationship_type IN ('father','mother','other')",
            name="ck_guardian_relationship_type",
        ),
        CheckConstraint("status IN ('active','inactive')", name="ck_guardian_status"),
    )


class StudentGuardian(Base):
    __tablename__ = "student_guardian"

    relation_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="CASCADE"), nullable=False
    )
    guardian_id: Mapped[str] = mapped_column(
        String, ForeignKey("guardian.guardian_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    bound_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    revoked_at: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        UniqueConstraint("student_id", "guardian_id", name="uq_student_guardian"),
        CheckConstraint(
            "status IN ('active','revoked')", name="ck_student_guardian_status"
        ),
    )


class GuardianChannelBinding(Base):
    __tablename__ = "guardian_channel_binding"

    binding_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String, nullable=False)
    guardian_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("guardian.guardian_id", ondelete="SET NULL")
    )
    active_student_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="SET NULL")
    )
    pending_state_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    bound_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("channel", "external_user_id", name="uq_guardian_channel_user"),
        CheckConstraint(
            "status IN ('pending','active','revoked')",
            name="ck_guardian_channel_binding_status",
        ),
    )


class GuardianInvitation(Base):
    __tablename__ = "guardian_invitation"

    invitation_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[str | None] = mapped_column(String)
    created_by_teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)


class StudentTeacherAssignment(Base):
    __tablename__ = "student_teacher_assignment"

    assignment_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="CASCADE"), nullable=False
    )
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('primary','subject','collaborator')",
            name="ck_student_teacher_assignment_role",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_student_teacher_assignment_dates",
        ),
        CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_student_teacher_assignment_status",
        ),
    )


class StudentImage(Base):
    __tablename__ = "student_image"

    image_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)
    source_position: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str] = mapped_column(String, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    uploaded_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    deleted_at: Mapped[str | None] = mapped_column(String)
    deleted_by_teacher_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint(
            "source_message_id", "source_position", name="uq_student_image_source"
        ),
        CheckConstraint(
            "status IN ('active','deleted')", name="ck_student_image_status"
        ),
    )


class PendingMediaAssignment(Base):
    __tablename__ = "pending_media_assignment"

    pending_id: Mapped[str] = mapped_column(String, primary_key=True)
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    caption: Mapped[str | None] = mapped_column(Text)
    media_json: Mapped[str] = mapped_column(Text, nullable=False)
    choices_json: Mapped[str] = mapped_column(Text, nullable=False)
    temporary_paths_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','completed','expired')",
            name="ck_pending_media_assignment_status",
        ),
    )


class FamilyConversation(Base):
    __tablename__ = "family_conversation"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    guardian_id: Mapped[str] = mapped_column(
        String, ForeignKey("guardian.guardian_id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id", ondelete="CASCADE"), nullable=False
    )
    assigned_teacher_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("teacher.teacher_id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    channel_conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    last_message_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "guardian_id",
            "student_id",
            "channel",
            "channel_conversation_id",
            name="uq_family_conversation_channel",
        ),
        CheckConstraint(
            "status IN ('active','waiting_teacher','closed')",
            name="ck_family_conversation_status",
        ),
    )


class FamilyMessage(Base):
    __tablename__ = "family_message"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("family_conversation.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String, nullable=False)
    sender_type: Mapped[str] = mapped_column(String, nullable=False)
    sender_id: Mapped[str | None] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("student_image.image_id", ondelete="SET NULL")
    )
    channel_message_id: Mapped[str | None] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    sent_at: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound','outbound')", name="ck_family_message_direction"
        ),
        CheckConstraint(
            "sender_type IN ('guardian','bot','teacher')",
            name="ck_family_message_sender_type",
        ),
        CheckConstraint(
            "status IN ('pending','completed','failed')",
            name="ck_family_message_status",
        ),
    )
