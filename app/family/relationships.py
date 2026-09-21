"""Student/teacher relationship lifecycle services."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
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
    if isinstance(start_date, datetime) or not isinstance(start_date, date):
        raise ValueError("开始日期无效")

    start = start_date.isoformat()
    existing = db.scalars(
        select(StudentTeacherAssignment).where(
            StudentTeacherAssignment.student_id == student_id,
            StudentTeacherAssignment.teacher_id == teacher_id,
            StudentTeacherAssignment.role == role,
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
        origin="manual",
    )
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("同一学生、教师和角色已有有效关系") from exc
    return assignment


def revoke_teacher_assignment(
    db: Session, assignment_id: str, end_date: date
) -> StudentTeacherAssignment:
    """Revoke an assignment on an inclusive end date."""
    assignment = db.get(StudentTeacherAssignment, assignment_id)
    if assignment is None:
        raise ValueError("教师关系不存在")
    if isinstance(end_date, datetime) or not isinstance(end_date, date):
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
    """Reconcile class-derived primary assignments with the active roster."""
    if on is not None and (isinstance(on, datetime) or not isinstance(on, date)):
        raise ValueError("同步日期无效")
    effective_date = on or date.today()
    effective = effective_date.isoformat()
    rows = db.execute(
        select(Enrollment.enrollment_id, Enrollment.student_id, Class.head_teacher_id)
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

    desired_sources: dict[tuple[str, str], set[int]] = {}
    for enrollment_id, student_id, teacher_id in rows:
        desired_sources.setdefault((student_id, teacher_id), set()).add(enrollment_id)
    active_assignments = db.scalars(
        select(StudentTeacherAssignment).where(
            StudentTeacherAssignment.status == "active",
            StudentTeacherAssignment.role == "primary",
        )
    ).all()
    active_keys = {(row.student_id, row.teacher_id) for row in active_assignments}
    changed = 0
    for assignment in active_assignments:
        if assignment.origin != "class_sync":
            continue
        key = (assignment.student_id, assignment.teacher_id)
        sources = desired_sources.get(key)
        if sources and assignment.source_enrollment_id in sources:
            continue
        if sources:
            assignment.source_enrollment_id = min(sources)
            assignment.updated_at = _utcnow()
            changed += 1
            continue
        assignment.status = "revoked"
        assignment.end_date = max(effective, assignment.start_date)
        assignment.updated_at = _utcnow()
        active_keys.discard(key)
        changed += 1

    for (student_id, teacher_id), enrollment_ids in desired_sources.items():
        if (student_id, teacher_id) in active_keys:
            continue
        db.add(
            StudentTeacherAssignment(
                student_id=student_id,
                teacher_id=teacher_id,
                role="primary",
                start_date=effective,
                status="active",
                origin="class_sync",
                source_enrollment_id=min(enrollment_ids),
            )
        )
        changed += 1
    if changed:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("教师关系同步发生并发冲突，请重试") from exc
    return changed
