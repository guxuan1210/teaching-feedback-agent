"""Student/teacher relationship lifecycle services."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.family.models import StudentTeacherAssignment


ASSIGNMENT_ROLES = {"primary", "subject", "collaborator"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_student(db: Session, student_id: str) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    if student.status != "active":
        raise ValueError("学生已停用")
    return student


def _active_teacher(db: Session, teacher_id: str) -> Teacher:
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    if teacher.status != "active":
        raise ValueError("教师已停用")
    return teacher


def _intervals_overlap(
    first_start: str,
    first_end: str | None,
    second_start: str,
    second_end: str | None,
) -> bool:
    return (first_end is None or second_start <= first_end) and (
        second_end is None or first_start <= second_end
    )


def assign_teacher(
    db: Session,
    student_id: str,
    teacher_id: str,
    role: str,
    start_date: date,
) -> StudentTeacherAssignment:
    """Create an open-ended assignment after subject and overlap validation."""
    _active_student(db, student_id)
    _active_teacher(db, teacher_id)
    if role not in ASSIGNMENT_ROLES:
        raise ValueError("教师关系角色无效")
    if not isinstance(start_date, date):
        raise ValueError("开始日期无效")

    start = start_date.isoformat()
    existing = db.scalars(
        select(StudentTeacherAssignment).where(
            StudentTeacherAssignment.student_id == student_id,
            StudentTeacherAssignment.teacher_id == teacher_id,
            StudentTeacherAssignment.role == role,
            StudentTeacherAssignment.status == "active",
        )
    ).all()
    if any(_intervals_overlap(row.start_date, row.end_date, start, None) for row in existing):
        raise ValueError("同一学生、教师和角色的有效期不能重叠")

    assignment = StudentTeacherAssignment(
        student_id=student_id,
        teacher_id=teacher_id,
        role=role,
        start_date=start,
        end_date=None,
        status="active",
    )
    db.add(assignment)
    db.commit()
    return assignment


def revoke_teacher_assignment(
    db: Session, assignment_id: str, end_date: date
) -> StudentTeacherAssignment:
    """Revoke an assignment on an inclusive end date."""
    assignment = db.get(StudentTeacherAssignment, assignment_id)
    if assignment is None:
        raise ValueError("教师关系不存在")
    if not isinstance(end_date, date):
        raise ValueError("结束日期无效")
    end = end_date.isoformat()
    if end < assignment.start_date:
        raise ValueError("结束日期不能早于开始日期")
    if assignment.status == "revoked":
        return assignment
    assignment.end_date = end
    assignment.status = "revoked"
    assignment.updated_at = _utcnow()
    db.commit()
    return assignment


def sync_head_teacher_assignments(db: Session, on: date | None = None) -> int:
    """Add missing primary assignments for current active class enrollment."""
    effective_date = on or date.today()
    effective = effective_date.isoformat()
    rows = db.execute(
        select(Enrollment.student_id, Class.head_teacher_id)
        .join(Class, Class.class_id == Enrollment.class_id)
        .join(Student, Student.student_id == Enrollment.student_id)
        .join(Teacher, Teacher.teacher_id == Class.head_teacher_id)
        .where(
            Enrollment.status == "active",
            Enrollment.start_date <= effective,
            or_(Enrollment.end_date.is_(None), Enrollment.end_date >= effective),
            Class.status == "active",
            Class.head_teacher_id.is_not(None),
            Student.status == "active",
            Teacher.status == "active",
        )
        .distinct()
    ).all()

    added = 0
    for student_id, teacher_id in rows:
        exists = db.scalar(
            select(StudentTeacherAssignment.assignment_id)
            .where(
                StudentTeacherAssignment.student_id == student_id,
                StudentTeacherAssignment.teacher_id == teacher_id,
                StudentTeacherAssignment.role == "primary",
                StudentTeacherAssignment.status == "active",
                StudentTeacherAssignment.start_date <= effective,
                or_(
                    StudentTeacherAssignment.end_date.is_(None),
                    StudentTeacherAssignment.end_date >= effective,
                ),
            )
            .limit(1)
        )
        if exists is not None:
            continue
        db.add(
            StudentTeacherAssignment(
                student_id=student_id,
                teacher_id=teacher_id,
                role="primary",
                start_date=effective,
                status="active",
            )
        )
        added += 1
    if added:
        db.commit()
    return added
