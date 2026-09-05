"""Feedback tables: indicator dictionary, daily/special feedback, and their
many-to-many indicator associations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Indicator(Base):
    __tablename__ = "indicator"

    indicator_id: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DailyFeedback(Base):
    __tablename__ = "daily_feedback"

    feedback_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("class_session.session_id"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    rating_knowledge: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_habit: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_mindset: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("rating_knowledge BETWEEN 1 AND 5", name="ck_daily_knowledge"),
        CheckConstraint("rating_habit BETWEEN 1 AND 5", name="ck_daily_habit"),
        CheckConstraint("rating_mindset BETWEEN 1 AND 5", name="ck_daily_mindset"),
        CheckConstraint("status IN ('active','void')", name="ck_daily_status"),
        UniqueConstraint("session_id", "student_id", name="uq_daily_session_student"),
    )


class SpecialFeedback(Base):
    __tablename__ = "special_feedback"

    feedback_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("class_session.session_id"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    rating_skill: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_habit: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("rating_skill BETWEEN 1 AND 5", name="ck_special_skill"),
        CheckConstraint("rating_habit BETWEEN 1 AND 5", name="ck_special_habit"),
        CheckConstraint("status IN ('active','void')", name="ck_special_status"),
        UniqueConstraint("session_id", "student_id", name="uq_special_session_student"),
    )


class DailyFeedbackIndicator(Base):
    __tablename__ = "daily_feedback_indicator"

    feedback_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("daily_feedback.feedback_id", ondelete="CASCADE"),
        primary_key=True,
    )
    indicator_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("indicator.indicator_id", ondelete="CASCADE"),
        primary_key=True,
    )


class SpecialFeedbackIndicator(Base):
    __tablename__ = "special_feedback_indicator"

    feedback_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("special_feedback.feedback_id", ondelete="CASCADE"),
        primary_key=True,
    )
    indicator_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("indicator.indicator_id", ondelete="CASCADE"),
        primary_key=True,
    )
