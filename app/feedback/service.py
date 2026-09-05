"""Feedback use cases: validate and persist daily/special feedback, resolve
the next unfinished student, and group the indicator dictionary by category."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Student, Teacher
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


@dataclass
class HistoryFilters:
    date_from: str | None = None
    date_to: str | None = None
    session_type: str | None = None
    class_id: str | None = None
    student_id: str | None = None
    status: str | None = None


@dataclass
class HistoryRow:
    feedback_id: str
    feedback_type: str
    session_id: str
    student_id: str
    student_name: str
    class_name: str
    teacher_name: str
    session_date: str
    start_time: str
    course_name: str | None
    ratings: dict[str, int]
    note: str | None
    status: str
    indicator_ids: list[str]
    indicator_texts: list[str]


def _ratings_for(feedback, feedback_type: str) -> dict[str, int]:
    if feedback_type == "daily":
        return {
            "rating_knowledge": feedback.rating_knowledge,
            "rating_habit": feedback.rating_habit,
            "rating_mindset": feedback.rating_mindset,
        }
    return {
        "rating_skill": feedback.rating_skill,
        "rating_habit": feedback.rating_habit,
    }


def _indicator_texts(db: Session, indicator_ids: list[str]) -> list[str]:
    if not indicator_ids:
        return []
    rows = db.scalars(
        select(Indicator).where(Indicator.indicator_id.in_(indicator_ids))
    ).all()
    return [row.text for row in rows]


def _apply_history_filters(stmt, model, filters: HistoryFilters):
    if filters.session_type:
        stmt = stmt.where(ClassSession.session_type == filters.session_type)
    if filters.class_id:
        stmt = stmt.where(ClassSession.class_id == filters.class_id)
    if filters.student_id:
        stmt = stmt.where(model.student_id == filters.student_id)
    if filters.date_from:
        stmt = stmt.where(ClassSession.session_date >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(ClassSession.session_date <= filters.date_to)
    if filters.status == "all":
        pass
    elif filters.status:
        stmt = stmt.where(model.status == filters.status)
    else:
        stmt = stmt.where(model.status == "active")
    return stmt


def query_history(db: Session, filters: HistoryFilters) -> list[HistoryRow]:
    rows: list[HistoryRow] = []
    for model, junction, feedback_type in (
        (DailyFeedback, DailyFeedbackIndicator, "daily"),
        (SpecialFeedback, SpecialFeedbackIndicator, "special"),
    ):
        stmt = (
            select(model, ClassSession, Student, Class, Teacher)
            .join(ClassSession, model.session_id == ClassSession.session_id)
            .join(Student, model.student_id == Student.student_id)
            .join(Class, ClassSession.class_id == Class.class_id)
            .join(Teacher, ClassSession.teacher_id == Teacher.teacher_id)
        )
        stmt = _apply_history_filters(stmt, model, filters)
        stmt = stmt.order_by(
            ClassSession.session_date.desc(),
            ClassSession.start_time.desc(),
            Student.name,
        )
        for feedback, session, student, klass, teacher in db.execute(stmt).all():
            indicator_ids = list(
                db.scalars(
                    select(junction.indicator_id).where(
                        junction.feedback_id == feedback.feedback_id
                    )
                )
            )
            rows.append(
                HistoryRow(
                    feedback_id=feedback.feedback_id,
                    feedback_type=feedback_type,
                    session_id=session.session_id,
                    student_id=student.student_id,
                    student_name=student.name,
                    class_name=klass.name,
                    teacher_name=teacher.name,
                    session_date=session.session_date,
                    start_time=session.start_time,
                    course_name=session.course_name,
                    ratings=_ratings_for(feedback, feedback_type),
                    note=feedback.note,
                    status=feedback.status,
                    indicator_ids=indicator_ids,
                    indicator_texts=_indicator_texts(db, indicator_ids),
                )
            )
    return rows


def update_daily_feedback(
    db: Session, feedback_id: str, values: DailyFeedbackInput
) -> DailyFeedback:
    feedback = db.get(DailyFeedback, feedback_id)
    if feedback is None:
        raise ValueError("反馈记录不存在")
    ratings = {
        "rating_knowledge": values.rating_knowledge,
        "rating_habit": values.rating_habit,
        "rating_mindset": values.rating_mindset,
    }
    for rating in ratings.values():
        _validate_rating(rating)
    indicator_ids = list(values.progress_indicators) + list(values.weak_indicators)
    _validate_indicators(db, indicator_ids, set(DAILY_CATEGORIES))
    for field, value in ratings.items():
        setattr(feedback, field, value)
    feedback.note = values.note
    feedback.updated_at = _utcnow()
    db.execute(
        delete(DailyFeedbackIndicator).where(
            DailyFeedbackIndicator.feedback_id == feedback_id
        )
    )
    db.add_all(
        [
            DailyFeedbackIndicator(feedback_id=feedback_id, indicator_id=iid)
            for iid in indicator_ids
        ]
    )
    db.commit()
    return feedback


def update_special_feedback(
    db: Session, feedback_id: str, values: SpecialFeedbackInput
) -> SpecialFeedback:
    feedback = db.get(SpecialFeedback, feedback_id)
    if feedback is None:
        raise ValueError("反馈记录不存在")
    ratings = {
        "rating_skill": values.rating_skill,
        "rating_habit": values.rating_habit,
    }
    for rating in ratings.values():
        _validate_rating(rating)
    indicator_ids = list(values.progress_indicators) + list(values.weak_indicators)
    _validate_indicators(db, indicator_ids, set(SPECIAL_CATEGORIES))
    for field, value in ratings.items():
        setattr(feedback, field, value)
    feedback.note = values.note
    feedback.updated_at = _utcnow()
    db.execute(
        delete(SpecialFeedbackIndicator).where(
            SpecialFeedbackIndicator.feedback_id == feedback_id
        )
    )
    db.add_all(
        [
            SpecialFeedbackIndicator(feedback_id=feedback_id, indicator_id=iid)
            for iid in indicator_ids
        ]
    )
    db.commit()
    return feedback


def void_feedback(db: Session, feedback_type: str, feedback_id: str) -> None:
    if feedback_type == "daily":
        model = DailyFeedback
    elif feedback_type == "special":
        model = SpecialFeedback
    else:
        raise ValueError("反馈类型无效")
    feedback = db.get(model, feedback_id)
    if feedback is None:
        raise ValueError("反馈记录不存在")
    feedback.status = "void"
    feedback.updated_at = _utcnow()
    db.commit()
