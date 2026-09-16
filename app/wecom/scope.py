from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.core.auth import is_admin


@dataclass(frozen=True)
class ScopeChoice:
    scope_type: str
    class_id: str
    class_name: str
    student_id: str | None
    student_name: str | None
    date_from: str
    date_to: str

    @property
    def label(self) -> str:
        if self.student_name:
            return f"{self.student_name}（{self.class_name}）"
        return self.class_name


@dataclass(frozen=True)
class ScopeResolution:
    status: str
    scope: ScopeChoice | None = None
    candidates: list[ScopeChoice] = field(default_factory=list)


def _dates(today: date) -> tuple[str, str]:
    return (today - timedelta(days=27)).isoformat(), today.isoformat()


def _allowed_classes(db: Session, teacher: Teacher) -> list[Class]:
    stmt = select(Class).where(Class.status == "active")
    if not is_admin(teacher):
        stmt = stmt.where(Class.head_teacher_id == teacher.teacher_id)
    return list(db.scalars(stmt.order_by(Class.name, Class.class_id)))


def _student_choices(
    db: Session, classes: list[Class], text: str, start: str, end: str
) -> list[ScopeChoice]:
    class_map = {row.class_id: row for row in classes}
    if not class_map:
        return []
    stmt = (
        select(Student, Enrollment.class_id)
        .join(Enrollment, Enrollment.student_id == Student.student_id)
        .where(
            Student.status == "active",
            Enrollment.status == "active",
            Enrollment.class_id.in_(class_map),
            Enrollment.start_date <= end,
            or_(Enrollment.end_date.is_(None), Enrollment.end_date >= end),
        )
        .order_by(Student.name, Student.student_id, Enrollment.class_id)
    )
    choices: list[ScopeChoice] = []
    seen: set[tuple[str, str]] = set()
    for student, class_id in db.execute(stmt):
        if not student.name or student.name not in text:
            continue
        key = (student.student_id, class_id)
        if key in seen:
            continue
        seen.add(key)
        klass = class_map[class_id]
        choices.append(
            ScopeChoice(
                "student", class_id, klass.name, student.student_id,
                student.name, start, end,
            )
        )
    return choices


def _class_choices(
    classes: list[Class], text: str, start: str, end: str
) -> list[ScopeChoice]:
    return [
        ScopeChoice("class", klass.class_id, klass.name, None, None, start, end)
        for klass in classes
        if klass.name and klass.name in text
    ]


def resolve_scope(
    db: Session,
    teacher: Teacher,
    text: str,
    *,
    today: date | None = None,
) -> ScopeResolution:
    current_date = today or date.today()
    start, end = _dates(current_date)
    classes = _allowed_classes(db, teacher)
    candidates = _student_choices(db, classes, text, start, end)
    candidates.extend(_class_choices(classes, text, start, end))
    if len(candidates) == 1:
        return ScopeResolution("resolved", candidates[0])
    if len(candidates) > 1:
        return ScopeResolution("scope_ambiguous", candidates=candidates)
    return ScopeResolution("scope_required")
