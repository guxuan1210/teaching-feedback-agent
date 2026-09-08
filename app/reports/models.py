"""Weekly report tables: the persisted report and its pinned source feedback."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WeeklyReport(Base):
    __tablename__ = "weekly_report"

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    class_id: Mapped[str] = mapped_column(
        String, ForeignKey("class.class_id"), nullable=False
    )
    teacher_id: Mapped[str] = mapped_column(
        String, ForeignKey("teacher.teacher_id"), nullable=False
    )
    period_start: Mapped[str] = mapped_column(String, nullable=False)
    period_end: Mapped[str] = mapped_column(String, nullable=False)
    generation_mode: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[str] = mapped_column(Text, nullable=False)
    concerns: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[str] = mapped_column(Text, nullable=False)
    generation_note: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="ck_report_period"),
        CheckConstraint(
            "generation_mode IN ('template','ai')", name="ck_report_mode"
        ),
        CheckConstraint("status IN ('draft','finalized')", name="ck_report_status"),
    )


class WeeklyReportSource(Base):
    __tablename__ = "weekly_report_source"

    report_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("weekly_report.report_id", ondelete="CASCADE"),
        primary_key=True,
    )
    feedback_type: Mapped[str] = mapped_column(String, primary_key=True)
    feedback_id: Mapped[str] = mapped_column(String, primary_key=True)

    __table_args__ = (
        CheckConstraint(
            "feedback_type IN ('daily','special')", name="ck_report_source_type"
        ),
    )
