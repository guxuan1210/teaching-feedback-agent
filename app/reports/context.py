"""Structured context for weekly report generation.

``ReportContext`` is an immutable, self-contained snapshot of the facts a
report generator is allowed to use. It is derived from the read-only profile
aggregation in :mod:`app.profiles.service` and never re-implements the
statistical queries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.catalog.models import Class, Student
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.profiles.service import IndicatorFrequency, build_student_profile
from app.reports.models import WeeklyReport


@dataclass(frozen=True)
class ReportContext:
    student_name: str
    class_name: str
    period_start: str
    period_end: str
    feedback_count: int
    rating_changes: dict[str, int]
    strengths: list[IndicatorFrequency]
    concerns: list[IndicatorFrequency]
    recent_notes: list[str]
    source_keys: list[tuple[str, str]]

    def to_prompt_payload(self) -> dict:
        """Return a plain dict suitable for serialization to an AI prompt."""
        return {
            "student_name": self.student_name,
            "class_name": self.class_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "feedback_count": self.feedback_count,
            "rating_changes": dict(self.rating_changes),
            "strengths": [
                {"text": item.text, "count": item.count} for item in self.strengths
            ],
            "concerns": [
                {"text": item.text, "count": item.count} for item in self.concerns
            ],
            "recent_notes": list(self.recent_notes),
        }


def build_report_context(
    db: Session,
    *,
    student_id: str,
    class_id: str,
    period_start: str,
    period_end: str,
) -> ReportContext:
    """Build a ``ReportContext`` from the active feedback in the period."""
    profile = build_student_profile(
        db,
        student_id=student_id,
        class_id=class_id,
        date_from=period_start,
        date_to=period_end,
    )

    if profile.feedback_count == 0:
        raise ValueError("周期内没有有效反馈")

    rating_changes: dict[str, int] = {}
    for key, trend in profile.daily_trends.items():
        rating_changes[key] = trend.change
    for key, trend in profile.special_trends.items():
        rating_changes[f"special_{key}"] = trend.change

    source_keys = [
        (source.feedback_type, source.feedback_id) for source in profile.sources
    ]

    return ReportContext(
        student_name=profile.student.name,
        class_name=profile.klass.name,
        period_start=period_start,
        period_end=period_end,
        feedback_count=profile.feedback_count,
        rating_changes=rating_changes,
        strengths=profile.strengths,
        concerns=profile.concerns,
        recent_notes=profile.recent_notes,
        source_keys=source_keys,
    )


def _parse_text_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _source_notes(db: Session, report: WeeklyReport) -> list[str]:
    notes: list[str] = []
    for source in report.sources:
        model = DailyFeedback if source.feedback_type == "daily" else SpecialFeedback
        feedback = db.get(model, source.feedback_id)
        if feedback is not None and feedback.note and feedback.note.strip():
            notes.append(feedback.note.strip())
    return notes


def build_rewrite_context(db: Session, report: WeeklyReport) -> ReportContext:
    """Rebuild a ``ReportContext`` from a persisted report's own facts.

    Used by rewriting so a candidate message is grounded in the report's pinned
    structured analysis rather than re-reading live feedback that may have
    changed since generation. Rating changes are omitted because they are not
    stored on the report, which keeps rewrites from inventing trend numbers.
    """
    student = db.get(Student, report.student_id)
    klass = db.get(Class, report.class_id)
    if student is None or klass is None:
        raise ValueError("周报关联的学生或班级不存在")

    def _indicator(text: str) -> IndicatorFrequency:
        return IndicatorFrequency(
            indicator_id="", text=text, category="", count=0
        )

    strengths = [_indicator(item) for item in _parse_text_list(report.strengths)]
    concerns = [_indicator(item) for item in _parse_text_list(report.concerns)]
    source_keys = [(s.feedback_type, s.feedback_id) for s in report.sources]

    return ReportContext(
        student_name=student.name,
        class_name=klass.name,
        period_start=report.period_start,
        period_end=report.period_end,
        feedback_count=len(source_keys),
        rating_changes={},
        strengths=strengths,
        concerns=concerns,
        recent_notes=_source_notes(db, report),
        source_keys=source_keys,
    )
