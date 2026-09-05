"""Session repository: create class sessions, list a day's sessions with
completion progress, and assemble the batch workspace (session + effective
roster + per-student feedback status)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Student, Teacher
from app.catalog.repository import active_roster
from app.core.ids import new_id
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.sessions.models import ClassSession

SESSION_TYPES = {"daily", "special"}


@dataclass
class ClassSessionSummary:
    session_id: str
    class_id: str
    class_name: str
    teacher_name: str
    session_type: str
    course_name: str | None
    session_date: str
    start_time: str
    completed: int
    total: int


@dataclass
class SessionWorkspace:
    session: ClassSession
    roster: list[Student]
    statuses: dict[str, str] = field(default_factory=dict)


def create_session(
    db: Session,
    *,
    class_id: str,
    teacher_id: str,
    session_type: str,
    session_date: date,
    start_time: time,
    course_name: str | None = None,
) -> ClassSession:
    if session_type not in SESSION_TYPES:
        raise ValueError("课程类型无效")
    klass = db.get(Class, class_id)
    if klass is None:
        raise ValueError("班级不存在")
    if klass.status != "active":
        raise ValueError("班级已停用")
    if klass.class_type != session_type:
        raise ValueError("课程类型与班级类型不匹配")
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    if teacher.status != "active":
        raise ValueError("教师已停用")
    if session_type == "special" and not (course_name or "").strip():
        raise ValueError("专项课需填写课程名称")

    session = ClassSession(
        session_id=new_id("SESSION"),
        class_id=class_id,
        teacher_id=teacher_id,
        session_type=session_type,
        course_name=(course_name or "").strip() or None,
        session_date=session_date.isoformat(),
        start_time=start_time.strftime("%H:%M"),
        status="active",
    )
    db.add(session)
    db.commit()
    return session


def _completed_count(db: Session, session: ClassSession) -> int:
    model = DailyFeedback if session.session_type == "daily" else SpecialFeedback
    return db.scalar(
        select(func.count())
        .select_from(model)
        .where(model.session_id == session.session_id, model.status == "active")
    ) or 0


def list_sessions_for_date(db: Session, session_date: date) -> list[ClassSessionSummary]:
    date_str = session_date.isoformat()
    sessions = db.scalars(
        select(ClassSession)
        .where(ClassSession.session_date == date_str, ClassSession.status == "active")
        .order_by(ClassSession.start_time)
    ).all()

    summaries: list[ClassSessionSummary] = []
    for session in sessions:
        klass = db.get(Class, session.class_id)
        teacher = db.get(Teacher, session.teacher_id)
        roster = active_roster(db, session.class_id, session_date)
        summaries.append(
            ClassSessionSummary(
                session_id=session.session_id,
                class_id=session.class_id,
                class_name=klass.name if klass else session.class_id,
                teacher_name=teacher.name if teacher else session.teacher_id,
                session_type=session.session_type,
                course_name=session.course_name,
                session_date=session.session_date,
                start_time=session.start_time,
                completed=_completed_count(db, session),
                total=len(roster),
            )
        )
    return summaries


def get_session_with_roster(db: Session, session_id: str) -> SessionWorkspace:
    session = db.get(ClassSession, session_id)
    if session is None:
        raise ValueError("场次不存在")
    on_date = date.fromisoformat(session.session_date)
    roster = active_roster(db, session.class_id, on_date)
    statuses: dict[str, str] = {}
    model = DailyFeedback if session.session_type == "daily" else SpecialFeedback
    for student in roster:
        existing = db.scalar(
            select(model).where(
                model.session_id == session_id,
                model.student_id == student.student_id,
                model.status == "active",
            )
        )
        statuses[student.student_id] = "已填写" if existing else "未填写"
    return SessionWorkspace(session=session, roster=roster, statuses=statuses)
