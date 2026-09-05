"""Catalog repository: create/list/update/deactivate for teachers, classes,
and students.

Every function takes a SQLAlchemy ``Session`` as its first argument so routes
can use the request-scoped ``get_db`` dependency. Create/update functions
validate inputs (blank names and out-of-range enumerations are rejected with
``ValueError``) before committing; deactivate functions never hard-delete.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Student, Teacher
from app.core.ids import new_id

CLASS_TYPES = {"daily", "special"}
STATUS_VALUES = {"active", "inactive"}

# Distinguishes "field not sent" (leave unchanged) from "field sent empty"
# (clear to NULL) in update functions.
_UNSET = object()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_name(name: str | None) -> str:
    value = (name or "").strip()
    if not value:
        raise ValueError("名称不能为空")
    return value


def _optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
def list_students(session: Session, active_only: bool = False) -> list[Student]:
    stmt = select(Student).order_by(Student.created_at)
    if active_only:
        stmt = stmt.where(Student.status == "active")
    return list(session.scalars(stmt))


def create_student(
    session: Session,
    name: str,
    grade: str | None = None,
    current_stage: str | None = None,
) -> Student:
    student = Student(
        student_id=new_id("S"),
        name=_clean_name(name),
        grade=_optional(grade),
        current_stage=_optional(current_stage),
        status="active",
    )
    session.add(student)
    session.commit()
    return student


def update_student(
    session: Session,
    student_id: str,
    name: str | None = None,
    grade: str | None = _UNSET,
    current_stage: str | None = _UNSET,
) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    if name is not None:
        student.name = _clean_name(name)
    if grade is not _UNSET:
        student.grade = _optional(grade)
    if current_stage is not _UNSET:
        student.current_stage = _optional(current_stage)
    student.updated_at = _utcnow()
    session.commit()
    return student


def deactivate_student(session: Session, student_id: str) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    student.status = "inactive"
    student.updated_at = _utcnow()
    session.commit()
    return student


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
def list_classes(session: Session, active_only: bool = False) -> list[Class]:
    stmt = select(Class).order_by(Class.created_at)
    if active_only:
        stmt = stmt.where(Class.status == "active")
    return list(session.scalars(stmt))


def create_class(
    session: Session,
    name: str,
    class_type: str,
    grade: str | None = None,
    head_teacher_id: str | None = None,
) -> Class:
    name = _clean_name(name)
    if class_type not in CLASS_TYPES:
        raise ValueError(f"课程类型无效：{class_type}")
    klass = Class(
        class_id=new_id("C"),
        name=name,
        grade=_optional(grade),
        class_type=class_type,
        head_teacher_id=_optional(head_teacher_id),
        status="active",
    )
    session.add(klass)
    session.commit()
    return klass


def update_class(
    session: Session,
    class_id: str,
    name: str | None = None,
    class_type: str | None = None,
    grade: str | None = _UNSET,
    head_teacher_id: str | None = _UNSET,
) -> Class:
    klass = session.get(Class, class_id)
    if klass is None:
        raise ValueError("班级不存在")
    if name is not None:
        klass.name = _clean_name(name)
    if class_type is not None:
        if class_type not in CLASS_TYPES:
            raise ValueError(f"课程类型无效：{class_type}")
        klass.class_type = class_type
    if grade is not _UNSET:
        klass.grade = _optional(grade)
    if head_teacher_id is not _UNSET:
        klass.head_teacher_id = _optional(head_teacher_id)
    klass.updated_at = _utcnow()
    session.commit()
    return klass


def deactivate_class(session: Session, class_id: str) -> Class:
    klass = session.get(Class, class_id)
    if klass is None:
        raise ValueError("班级不存在")
    klass.status = "inactive"
    klass.updated_at = _utcnow()
    session.commit()
    return klass


# ---------------------------------------------------------------------------
# Teachers
# ---------------------------------------------------------------------------
def list_teachers(session: Session, active_only: bool = False) -> list[Teacher]:
    stmt = select(Teacher).order_by(Teacher.created_at)
    if active_only:
        stmt = stmt.where(Teacher.status == "active")
    return list(session.scalars(stmt))


def create_teacher(
    session: Session,
    name: str,
    role: str | None = None,
) -> Teacher:
    teacher = Teacher(
        teacher_id=new_id("T"),
        name=_clean_name(name),
        role=_optional(role),
        status="active",
    )
    session.add(teacher)
    session.commit()
    return teacher


def update_teacher(
    session: Session,
    teacher_id: str,
    name: str | None = None,
    role: str | None = _UNSET,
) -> Teacher:
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    if name is not None:
        teacher.name = _clean_name(name)
    if role is not _UNSET:
        teacher.role = _optional(role)
    teacher.updated_at = _utcnow()
    session.commit()
    return teacher


def deactivate_teacher(session: Session, teacher_id: str) -> Teacher:
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    teacher.status = "inactive"
    teacher.updated_at = _utcnow()
    session.commit()
    return teacher
