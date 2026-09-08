"""Student growth profile aggregation.

The profile is a read-only roll-up over existing feedback: it never copies
student facts into a separate table. It gathers only active feedback whose
session date falls inside a closed interval, then computes rating trends,
high-frequency indicators, recent notes, and a traceable source list.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Enrollment, Student
from app.feedback.models import (
    DailyFeedback,
    DailyFeedbackIndicator,
    Indicator,
    SpecialFeedback,
    SpecialFeedbackIndicator,
)
from app.sessions.models import ClassSession


@dataclass(frozen=True)
class Trend:
    start: int
    end: int
    change: int


@dataclass(frozen=True)
class IndicatorFrequency:
    indicator_id: str
    text: str
    category: str
    count: int


@dataclass(frozen=True)
class ProfileSource:
    feedback_type: str
    feedback_id: str
    session_date: str
    note: str | None


@dataclass
class StudentProfile:
    student: Student
    klass: Class
    feedback_count: int
    daily_trends: dict[str, Trend]
    special_trends: dict[str, Trend]
    strengths: list[IndicatorFrequency]
    concerns: list[IndicatorFrequency]
    recent_notes: list[str]
    sources: list[ProfileSource]


_DAILY_TREND_ATTRS = (
    ("knowledge", "rating_knowledge"),
    ("habit", "rating_habit"),
    ("mindset", "rating_mindset"),
)

_SPECIAL_TREND_ATTRS = (
    ("skill", "rating_skill"),
    ("habit", "rating_habit"),
)


def _active_feedback(
    db: Session, model, student_id: str, class_id: str, date_from: str, date_to: str
):
    """Return ``(feedback, session)`` rows for active feedback in the interval."""
    stmt = (
        select(model, ClassSession)
        .join(ClassSession, model.session_id == ClassSession.session_id)
        .where(
            model.student_id == student_id,
            model.status == "active",
            ClassSession.class_id == class_id,
            ClassSession.session_date >= date_from,
            ClassSession.session_date <= date_to,
        )
    )
    return db.execute(stmt).all()


def _trend(first, last, attrs: tuple[tuple[str, str], ...]) -> dict[str, Trend]:
    trends: dict[str, Trend] = {}
    for key, attr in attrs:
        start = getattr(first, attr)
        end = getattr(last, attr)
        trends[key] = Trend(start=start, end=end, change=end - start)
    return trends


def build_student_profile(
    db: Session,
    *,
    student_id: str,
    class_id: str,
    date_from: str,
    date_to: str,
) -> StudentProfile:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")

    klass = db.get(Class, class_id)
    if klass is None:
        raise ValueError("班级不存在")

    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student_id,
            Enrollment.class_id == class_id,
        )
    )
    if enrollment is None:
        raise ValueError("学生从未加入该班级")

    daily_pairs = [
        (feedback, session)
        for feedback, session in _active_feedback(
            db, DailyFeedback, student_id, class_id, date_from, date_to
        )
    ]
    special_pairs = [
        (feedback, session)
        for feedback, session in _active_feedback(
            db, SpecialFeedback, student_id, class_id, date_from, date_to
        )
    ]

    daily_ordered = sorted(daily_pairs, key=lambda p: (p[1].session_date, p[1].start_time))
    special_ordered = sorted(
        special_pairs, key=lambda p: (p[1].session_date, p[1].start_time)
    )

    daily_trends: dict[str, Trend] = {}
    if daily_ordered:
        daily_trends = _trend(
            daily_ordered[0][0], daily_ordered[-1][0], _DAILY_TREND_ATTRS
        )

    special_trends: dict[str, Trend] = {}
    if special_ordered:
        special_trends = _trend(
            special_ordered[0][0], special_ordered[-1][0], _SPECIAL_TREND_ATTRS
        )

    daily_ids = [feedback.feedback_id for feedback, _ in daily_pairs]
    special_ids = [feedback.feedback_id for feedback, _ in special_pairs]

    indicator_counts: dict[str, int] = {}
    if daily_ids:
        for iid in db.scalars(
            select(DailyFeedbackIndicator.indicator_id).where(
                DailyFeedbackIndicator.feedback_id.in_(daily_ids)
            )
        ):
            indicator_counts[iid] = indicator_counts.get(iid, 0) + 1
    if special_ids:
        for iid in db.scalars(
            select(SpecialFeedbackIndicator.indicator_id).where(
                SpecialFeedbackIndicator.feedback_id.in_(special_ids)
            )
        ):
            indicator_counts[iid] = indicator_counts.get(iid, 0) + 1

    indicator_rows: dict[str, Indicator] = {}
    if indicator_counts:
        indicator_rows = {
            indicator.indicator_id: indicator
            for indicator in db.scalars(
                select(Indicator).where(Indicator.indicator_id.in_(indicator_counts))
            )
        }

    strength_items: list[tuple[IndicatorFrequency, int]] = []
    concern_items: list[tuple[IndicatorFrequency, int]] = []
    for iid, count in indicator_counts.items():
        indicator = indicator_rows.get(iid)
        if indicator is None:
            continue
        item = IndicatorFrequency(
            indicator_id=indicator.indicator_id,
            text=indicator.text,
            category=indicator.category,
            count=count,
        )
        if indicator.category.endswith("_progress"):
            strength_items.append((item, indicator.sort_order))
        elif indicator.category.endswith("_weak"):
            concern_items.append((item, indicator.sort_order))

    strength_items.sort(key=lambda t: (-t[0].count, t[1], t[0].indicator_id))
    concern_items.sort(key=lambda t: (-t[0].count, t[1], t[0].indicator_id))

    strengths = [item for item, _ in strength_items]
    concerns = [item for item, _ in concern_items]

    all_pairs = [(f, s, "daily") for f, s in daily_pairs] + [
        (f, s, "special") for f, s in special_pairs
    ]

    ordered = sorted(all_pairs, key=lambda t: (t[1].session_date, t[1].start_time))
    sources = [
        ProfileSource(
            feedback_type=feedback_type,
            feedback_id=feedback.feedback_id,
            session_date=session.session_date,
            note=feedback.note,
        )
        for feedback, session, feedback_type in ordered
    ]

    newest = sorted(
        all_pairs, key=lambda t: (t[1].session_date, t[1].start_time), reverse=True
    )
    recent_notes = [
        feedback.note
        for feedback, _session, _feedback_type in newest
        if feedback.note and feedback.note.strip()
    ][:10]

    return StudentProfile(
        student=student,
        klass=klass,
        feedback_count=len(daily_pairs) + len(special_pairs),
        daily_trends=daily_trends,
        special_trends=special_trends,
        strengths=strengths,
        concerns=concerns,
        recent_notes=recent_notes,
        sources=sources,
    )
