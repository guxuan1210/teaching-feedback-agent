"""Authorization lookups for teacher and guardian access to students."""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog.models import Student, Teacher
from app.family.models import Guardian, StudentGuardian, StudentTeacherAssignment


def teacher_can_access_student(
    db: Session,
    teacher_id: str,
    student_id: str,
    on: date | None = None,
) -> bool:
    """Return whether an active teacher has an effective active assignment."""
    effective_on = (on or date.today()).isoformat()
    stmt = (
        select(StudentTeacherAssignment.assignment_id)
        .join(Teacher, Teacher.teacher_id == StudentTeacherAssignment.teacher_id)
        .join(Student, Student.student_id == StudentTeacherAssignment.student_id)
        .where(
            StudentTeacherAssignment.teacher_id == teacher_id,
            StudentTeacherAssignment.student_id == student_id,
            StudentTeacherAssignment.status == "active",
            StudentTeacherAssignment.start_date <= effective_on,
            or_(
                StudentTeacherAssignment.end_date.is_(None),
                StudentTeacherAssignment.end_date >= effective_on,
            ),
            Teacher.status == "active",
            Student.status == "active",
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def guardian_can_access_student(
    db: Session, guardian_id: str, student_id: str
) -> bool:
    """Return whether an active guardian has an active student relation."""
    stmt = (
        select(StudentGuardian.relation_id)
        .join(Guardian, Guardian.guardian_id == StudentGuardian.guardian_id)
        .join(Student, Student.student_id == StudentGuardian.student_id)
        .where(
            StudentGuardian.guardian_id == guardian_id,
            StudentGuardian.student_id == student_id,
            StudentGuardian.status == "active",
            Guardian.status == "active",
            Student.status == "active",
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def list_students_for_guardian(db: Session, guardian_id: str) -> list[Student]:
    """List distinct accessible students in stable display order."""
    stmt = (
        select(Student)
        .join(StudentGuardian, StudentGuardian.student_id == Student.student_id)
        .join(Guardian, Guardian.guardian_id == StudentGuardian.guardian_id)
        .where(
            StudentGuardian.guardian_id == guardian_id,
            StudentGuardian.status == "active",
            Guardian.status == "active",
            Student.status == "active",
        )
        .order_by(Student.name, Student.student_id)
    )
    return list(db.scalars(stmt).unique())
