"""Session table: a single taught class period (one daily or special session)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClassSession(Base):
    __tablename__ = "class_session"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    class_id: Mapped[str] = mapped_column(
        String, ForeignKey("class.class_id"), nullable=False
    )
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    session_type: Mapped[str] = mapped_column(String, nullable=False)
    course_name: Mapped[str | None] = mapped_column(String)
    session_date: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "session_type IN ('daily','special')", name="ck_class_session_type"
        ),
        CheckConstraint(
            "status IN ('active','cancelled')", name="ck_class_session_status"
        ),
    )
