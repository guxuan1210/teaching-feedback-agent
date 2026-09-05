"""Feedback use cases: validate and persist daily/special feedback, resolve
the next unfinished student, and group the indicator dictionary by category."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.catalog.repository import active_roster
from app.core.ids import new_id
from app.feedback.forms import DailyFeedbackInput, SpecialFeedbackInput
from app.feedback.models import (
    DailyFeedback,
    DailyFeedbackIndicator,
    Indicator,
    SpecialFeedback,
    SpecialFeedbackIndicator,
)
from app.sessions.models import ClassSession

DAILY_CATEGORIES = [
    "daily_k_progress",
    "daily_k_weak",
    "daily_h_progress",
    "daily_h_weak",
    "daily_m_progress",
    "daily_m_weak",
]
SPECIAL_CATEGORIES = [
    "special_k_progress",
    "special_k_weak",
    "special_h_progress",
    "special_h_weak",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_rating(rating: int) -> None:
    if not (1 <= rating <= 5):
        raise ValueError("评分必须是1到5")


def _get_session(db: Session, session_id: str, expected_type: str) -> ClassSession:
    session = db.get(ClassSession, session_id)
    if session is None:
        raise ValueError("场次不存在")
    if session.session_type != expected_type:
        raise ValueError("场次类型不匹配")
    return session


def _validate_enrollment(db: Session, session: ClassSession, student_id: str) -> None:
    on_date = date.fromisoformat(session.session_date)
    roster_ids = {s.student_id for s in active_roster(db, session.class_id, on_date)}
    if student_id not in roster_ids:
        raise ValueError("学生不在该场次的有效名单中")


def _validate_indicators(db: Session, indicator_ids: list[str], allowed: set[str]) -> None:
    for iid in indicator_ids:
        indicator = db.get(Indicator, iid)
        if indicator is None or indicator.category not in allowed:
            raise ValueError("指标无效或不属于该反馈类型")


def indicators_by_category(
    db: Session, categories: list[str]
) -> dict[str, list[Indicator]]:
    rows = db.scalars(
        select(Indicator)
        .where(Indicator.active == 1, Indicator.category.in_(categories))
        .order_by(Indicator.category, Indicator.sort_order)
    ).all()
    return {cat: [r for r in rows if r.category == cat] for cat in categories}


def _save_feedback(
    db: Session,
    session: ClassSession,
    student_id: str,
    model,
    junction_model,
    ratings: dict[str, int],
    indicator_ids: list[str],
    note: str | None,
):
    existing = db.scalar(
        select(model).where(
            model.session_id == session.session_id,
            model.student_id == student_id,
            model.status == "active",
        )
    )
    if existing is not None:
        for field, value in ratings.items():
            setattr(existing, field, value)
        existing.note = note
        existing.updated_at = _utcnow()
        db.execute(
            delete(junction_model).where(junction_model.feedback_id == existing.feedback_id)
        )
        db.add_all(
            [junction_model(feedback_id=existing.feedback_id, indicator_id=iid) for iid in indicator_ids]
        )
        db.commit()
        return existing

    feedback = model(
        feedback_id=new_id("F"),
        session_id=session.session_id,
        student_id=student_id,
        note=note,
        status="active",
        **ratings,
    )
    db.add(feedback)
    db.flush()
    db.add_all(
        [junction_model(feedback_id=feedback.feedback_id, indicator_id=iid) for iid in indicator_ids]
    )
    db.commit()
    return feedback


def save_daily_feedback(
    db: Session, session_id: str, student_id: str, values: DailyFeedbackInput
) -> DailyFeedback:
    session = _get_session(db, session_id, "daily")
    _validate_enrollment(db, session, student_id)
    ratings = {
        "rating_knowledge": values.rating_knowledge,
        "rating_habit": values.rating_habit,
        "rating_mindset": values.rating_mindset,
    }
    for rating in ratings.values():
        _validate_rating(rating)
    indicator_ids = list(values.progress_indicators) + list(values.weak_indicators)
    _validate_indicators(db, indicator_ids, set(DAILY_CATEGORIES))
    return _save_feedback(
        db, session, student_id, DailyFeedback, DailyFeedbackIndicator,
        ratings, indicator_ids, values.note,
    )


def save_special_feedback(
    db: Session, session_id: str, student_id: str, values: SpecialFeedbackInput
) -> SpecialFeedback:
    session = _get_session(db, session_id, "special")
    _validate_enrollment(db, session, student_id)
    ratings = {
        "rating_skill": values.rating_skill,
        "rating_habit": values.rating_habit,
    }
    for rating in ratings.values():
        _validate_rating(rating)
    indicator_ids = list(values.progress_indicators) + list(values.weak_indicators)
    _validate_indicators(db, indicator_ids, set(SPECIAL_CATEGORIES))
    return _save_feedback(
        db, session, student_id, SpecialFeedback, SpecialFeedbackIndicator,
        ratings, indicator_ids, values.note,
    )


def next_unfinished_student(
    db: Session, session_id: str, after_student_id: str
) -> str | None:
    session = db.get(ClassSession, session_id)
    if session is None:
        return None
    on_date = date.fromisoformat(session.session_date)
    roster = active_roster(db, session.class_id, on_date)
    model = DailyFeedback if session.session_type == "daily" else SpecialFeedback
    ids = [s.student_id for s in roster]
    try:
        start = ids.index(after_student_id) + 1
    except ValueError:
        start = 0
    for sid in ids[start:]:
        existing = db.scalar(
            select(model).where(
                model.session_id == session_id,
                model.student_id == sid,
                model.status == "active",
            )
        )
        if existing is None:
            return sid
    return None
