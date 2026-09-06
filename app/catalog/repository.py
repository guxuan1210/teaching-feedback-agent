"""Catalog repository: create/list/update/deactivate for teachers, classes,
and students.

Every function takes a SQLAlchemy ``Session`` as its first argument so routes
can use the request-scoped ``get_db`` dependency. Create/update functions
validate inputs (blank names and out-of-range enumerations are rejected with
``ValueError``) before committing; deactivate functions never hard-delete.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    Class,
    Enrollment,
    LateCareLevelDict,
    StageDict,
    Student,
    Teacher,
)
from app.core.ids import new_id
from app.core.security import hash_password

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
def list_active_stages(session: Session) -> list[StageDict]:
    stmt = (
        select(StageDict)
        .where(StageDict.active == 1)
        .order_by(StageDict.sort_order, StageDict.stage_id)
    )
    return list(session.scalars(stmt))


def list_active_late_care_levels(session: Session) -> list[LateCareLevelDict]:
    stmt = (
        select(LateCareLevelDict)
        .where(LateCareLevelDict.active == 1)
        .order_by(LateCareLevelDict.sort_order, LateCareLevelDict.level_id)
    )
    return list(session.scalars(stmt))


def _validated_stage(session: Session, value: str | None) -> str | None:
    value = _optional(value)
    if value is None:
        return None
    row = session.get(StageDict, value)
    if row is None or row.active != 1:
        raise ValueError(f"九阶阶段无效：{value}")
    return value


def _validated_late_care_level(session: Session, value: str | None) -> str | None:
    value = _optional(value)
    if value is None:
        return None
    row = session.get(LateCareLevelDict, value)
    if row is None or row.active != 1:
        raise ValueError(f"晚辅档位无效：{value}")
    return value


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
    late_care_level: str | None = None,
) -> Student:
    student = Student(
        student_id=new_id("S"),
        name=_clean_name(name),
        grade=_optional(grade),
        current_stage=_validated_stage(session, current_stage),
        late_care_level=_validated_late_care_level(session, late_care_level),
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
    late_care_level: str | None = _UNSET,
) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    if name is not None:
        student.name = _clean_name(name)
    if grade is not _UNSET:
        student.grade = _optional(grade)
    if current_stage is not _UNSET:
        student.current_stage = _validated_stage(session, current_stage)
    if late_care_level is not _UNSET:
        student.late_care_level = _validated_late_care_level(session, late_care_level)
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
    password: str | None = None,
) -> Teacher:
    teacher = Teacher(
        teacher_id=new_id("T"),
        name=_clean_name(name),
        role=_optional(role),
        password_hash=hash_password(password) if password else None,
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
    password: str | None = None,
) -> Teacher:
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    if name is not None:
        teacher.name = _clean_name(name)
    if role is not _UNSET:
        teacher.role = _optional(role)
    if password:
        teacher.password_hash = hash_password(password)
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


# ---------------------------------------------------------------------------
# Enrollments (effective-dated membership)
# ---------------------------------------------------------------------------
def enroll_student(
    session: Session,
    student_id: str,
    class_id: str,
    start_date: date,
) -> Enrollment:
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    if student.status != "active":
        raise ValueError("学生已停用")
    klass = session.get(Class, class_id)
    if klass is None:
        raise ValueError("班级不存在")
    if klass.status != "active":
        raise ValueError("班级已停用")
    new_start = start_date.isoformat()
    existing = session.scalars(
        select(Enrollment).where(
            Enrollment.student_id == student_id,
            Enrollment.class_id == class_id,
        )
    ).all()
    for row in existing:
        # A new enrollment is open-ended (end_date=None == infinity), so it
        # overlaps any prior interval that reaches past new_start.
        if row.end_date is None or row.end_date >= new_start:
            raise ValueError("已在该班级的有效期内")
    enrollment = Enrollment(
        student_id=student_id,
        class_id=class_id,
        start_date=new_start,
        end_date=None,
        status="active",
    )
    session.add(enrollment)
    session.commit()
    return enrollment


def leave_class(session: Session, enrollment_id: int, end_date: date) -> Enrollment:
    enrollment = session.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise ValueError("入班记录不存在")
    if enrollment.end_date is not None:
        raise ValueError("该学生已离班")
    if end_date.isoformat() < enrollment.start_date:
        raise ValueError("离班日期不能早于入班日期")
    enrollment.end_date = end_date.isoformat()
    enrollment.status = "left"
    enrollment.updated_at = _utcnow()
    session.commit()
    return enrollment


def active_roster(session: Session, class_id: str, on_date: date) -> list[Student]:
    on = on_date.isoformat()
    stmt = (
        select(Student)
        .join(Enrollment, Enrollment.student_id == Student.student_id)
        .where(
            Enrollment.class_id == class_id,
            Enrollment.start_date <= on,
            or_(Enrollment.end_date.is_(None), Enrollment.end_date >= on),
        )
        .order_by(Student.name)
    )
    return list(session.scalars(stmt).unique())


def list_enrollments(session: Session, class_id: str | None = None) -> list[Enrollment]:
    stmt = (
        select(Enrollment)
        .join(Student, Student.student_id == Enrollment.student_id)
        .join(Class, Class.class_id == Enrollment.class_id)
        .order_by(Enrollment.start_date, Enrollment.enrollment_id)
    )
    if class_id:
        stmt = stmt.where(Enrollment.class_id == class_id)
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Teacher-scoped lookups (a teacher owns the classes they head)
# ---------------------------------------------------------------------------
def list_classes_for_teacher(session: Session, teacher_id: str) -> list[Class]:
    stmt = (
        select(Class)
        .where(Class.head_teacher_id == teacher_id, Class.status == "active")
        .order_by(Class.created_at)
    )
    return list(session.scalars(stmt))


def list_students_for_teacher(session: Session, teacher_id: str) -> list[Student]:
    stmt = (
        select(Student)
        .join(Enrollment, Enrollment.student_id == Student.student_id)
        .join(Class, Class.class_id == Enrollment.class_id)
        .where(Class.head_teacher_id == teacher_id)
        .order_by(Student.name)
    )
    return list(session.scalars(stmt).unique())
