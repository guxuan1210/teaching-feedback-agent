"""Structured context for weekly report generation.

``ReportContext`` is an immutable, self-contained snapshot of the facts a
report generator is allowed to use. It is derived from the read-only profile
aggregation in :mod:`app.profiles.service` and never re-implements the
statistical queries.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.profiles.service import IndicatorFrequency, build_student_profile


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
