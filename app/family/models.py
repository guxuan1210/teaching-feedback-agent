"""Family relationships, student media, and guardian communication tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_id


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Guardian(Base):
    __tablename__ = "guardian"

    guardian_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("G")
    )
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

    relation_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("SG")
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    guardian_id: Mapped[str] = mapped_column(
        String, ForeignKey("guardian.guardian_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    bound_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    revoked_at: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        UniqueConstraint("student_id", "guardian_id", name="uq_student_guardian"),
        CheckConstraint(
            "status IN ('active','revoked')", name="ck_student_guardian_status"
        ),
        Index("ix_student_guardian_guardian_status", "guardian_id", "status"),
    )


class GuardianChannelBinding(Base):
    __tablename__ = "guardian_channel_binding"

    binding_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("GCB")
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String, nullable=False)
    guardian_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("guardian.guardian_id")
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
        CheckConstraint(
            "status != 'active' OR guardian_id IS NOT NULL",
            name="ck_guardian_channel_binding_active_guardian",
        ),
    )


class GuardianInvitation(Base):
    __tablename__ = "guardian_invitation"

    invitation_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("GI")
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[str | None] = mapped_column(String)
    created_by_teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)

    __table_args__ = (
        CheckConstraint("max_uses > 0", name="ck_guardian_invitation_max_uses"),
        CheckConstraint("used_count >= 0", name="ck_guardian_invitation_used_count"),
        CheckConstraint(
            "used_count <= max_uses", name="ck_guardian_invitation_uses_limit"
        ),
    )


class StudentTeacherAssignment(Base):
    __tablename__ = "student_teacher_assignment"

    assignment_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("STA")
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    origin: Mapped[str] = mapped_column(String, nullable=False, default="manual", server_default="manual")
    source_enrollment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("enrollment.enrollment_id")
    )
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
        CheckConstraint(
            "(origin = 'manual' AND source_enrollment_id IS NULL) OR "
            "(origin = 'class_sync' AND source_enrollment_id IS NOT NULL AND role = 'primary')",
            name="ck_student_teacher_assignment_origin",
        ),
        Index(
            "uq_student_teacher_active_role",
            "student_id", "teacher_id", "role",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_student_teacher_assignment_lookup",
            "teacher_id",
            "student_id",
            "status",
        ),
    )


class StudentImage(Base):
    __tablename__ = "student_image"

    image_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("IMG")
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
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
        String, ForeignKey("teacher.teacher_id")
    )
    quarantine_path: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        UniqueConstraint(
            "source_message_id", "source_position", name="uq_student_image_source"
        ),
        CheckConstraint(
            "status IN ('active','deleted')", name="ck_student_image_status"
        ),
        CheckConstraint(
            "(status = 'active' AND deleted_at IS NULL "
            "AND deleted_by_teacher_id IS NULL AND quarantine_path IS NULL) OR "
            "(status = 'deleted' AND deleted_at IS NOT NULL "
            "AND deleted_by_teacher_id IS NOT NULL AND quarantine_path IS NOT NULL)",
            name="ck_student_image_deletion_metadata",
        ),
        Index(
            "ix_student_image_student_status_uploaded",
            "student_id",
            "status",
            "uploaded_at",
        ),
    )


class PendingMediaAssignment(Base):
    __tablename__ = "pending_media_assignment"

    pending_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("PMA")
    )
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
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

    conversation_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("FC")
    )
    guardian_id: Mapped[str] = mapped_column(
        String, ForeignKey("guardian.guardian_id"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
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

    message_id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("FM")
    )
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("family_conversation.conversation_id"),
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
    reply_to_message_id: Mapped[str | None] = mapped_column(String, index=True)
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
        CheckConstraint(
            "(direction = 'inbound' AND sender_type = 'guardian') OR "
            "(direction = 'outbound' AND sender_type IN ('bot','teacher'))",
            name="ck_family_message_direction_sender",
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_reason IS NOT NULL) OR "
            "(status != 'failed' AND failure_reason IS NULL)",
            name="ck_family_message_failure_reason",
        ),
        CheckConstraint(
            "direction != 'outbound' OR status != 'completed' OR sent_at IS NOT NULL",
            name="ck_family_message_completed_sent_at",
        ),
        CheckConstraint(
            "status != 'pending' OR sent_at IS NULL",
            name="ck_family_message_pending_sent_at",
        ),
        CheckConstraint(
            "(sender_type = 'bot' AND sender_id IS NULL) OR "
            "(sender_type IN ('guardian','teacher') AND sender_id IS NOT NULL)",
            name="ck_family_message_sender_id",
        ),
        Index(
            "ix_family_message_conversation_created", "conversation_id", "created_at"
        ),
    )
