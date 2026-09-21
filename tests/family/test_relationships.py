from __future__ import annotations

from datetime import date

import pytest

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.family.models import Guardian, StudentGuardian, StudentTeacherAssignment
from app.family.permissions import (
    guardian_can_access_student,
    list_students_for_guardian,
    teacher_can_access_student,
)
from app.family.relationships import (
    assign_teacher,
    revoke_teacher_assignment,
    sync_head_teacher_assignments,
)


def _teacher(db, teacher_id: str, *, role: str = "晚辅教师", status: str = "active"):
    row = Teacher(teacher_id=teacher_id, name=teacher_id, role=role, status=status)
    db.add(row)
    db.commit()
    return row


def _student(db, student_id: str, name: str | None = None, *, status="active"):
    row = Student(student_id=student_id, name=name or student_id, status=status)
    db.add(row)
    db.commit()
    return row


def test_syncs_current_head_teacher_assignment_and_is_idempotent(db_session):
    teacher = _teacher(db_session, "T1")
    student = _student(db_session, "S1")
    db_session.add(
        Class(
            class_id="C1", name="一班", class_type="daily",
            head_teacher_id=teacher.teacher_id, status="active",
        )
    )
    db_session.commit()
    db_session.add(
        Enrollment(
            student_id=student.student_id, class_id="C1",
            start_date="2026-09-01", status="active",
        )
    )
    db_session.commit()

    assert sync_head_teacher_assignments(db_session, on=date(2026, 9, 21)) == 1
    assert sync_head_teacher_assignments(db_session, on=date(2026, 9, 21)) == 0
    assignment = db_session.query(StudentTeacherAssignment).one()
    assert (assignment.student_id, assignment.teacher_id, assignment.role) == (
        "S1", "T1", "primary"
    )
    assert assignment.start_date == "2026-09-21"


def test_sync_ignores_historical_enrollment(db_session):
    teacher = _teacher(db_session, "T1")
    student = _student(db_session, "S1")
    db_session.add(
        Class(
            class_id="C1", name="一班", class_type="daily",
            head_teacher_id=teacher.teacher_id, status="active",
        )
    )
    db_session.commit()
    db_session.add(
        Enrollment(
            student_id=student.student_id, class_id="C1", start_date="2026-01-01",
            end_date="2026-08-31", status="left",
        )
    )
    db_session.commit()

    assert sync_head_teacher_assignments(db_session, on=date(2026, 9, 21)) == 0
    assert db_session.query(StudentTeacherAssignment).count() == 0


def test_teacher_permission_honors_inclusive_date_boundaries_and_revocation(db_session):
    teacher = _teacher(db_session, "T1")
    student = _student(db_session, "S1")
    assignment = assign_teacher(
        db_session, student.student_id, teacher.teacher_id, "subject", date(2026, 9, 1)
    )
    assignment.end_date = "2026-09-30"
    db_session.commit()

    assert not teacher_can_access_student(db_session, "T1", "S1", date(2026, 8, 31))
    assert teacher_can_access_student(db_session, "T1", "S1", date(2026, 9, 1))
    assert teacher_can_access_student(db_session, "T1", "S1", date(2026, 9, 30))
    assert not teacher_can_access_student(db_session, "T1", "S1", date(2026, 10, 1))

    revoke_teacher_assignment(db_session, assignment.assignment_id, date(2026, 9, 15))
    assert not teacher_can_access_student(db_session, "T1", "S1", date(2026, 9, 15))


def test_assignment_rejects_inactive_subjects_invalid_values_and_overlap(db_session):
    active_teacher = _teacher(db_session, "T1")
    inactive_teacher = _teacher(db_session, "T2", status="inactive")
    active_student = _student(db_session, "S1")
    inactive_student = _student(db_session, "S2", status="inactive")

    with pytest.raises(ValueError, match="教师.*停用"):
        assign_teacher(db_session, "S1", inactive_teacher.teacher_id, "primary", date.today())
    with pytest.raises(ValueError, match="学生.*停用"):
        assign_teacher(db_session, inactive_student.student_id, "T1", "primary", date.today())
    with pytest.raises(ValueError, match="角色"):
        assign_teacher(db_session, active_student.student_id, "T1", "owner", date.today())

    first = assign_teacher(db_session, "S1", active_teacher.teacher_id, "primary", date(2026, 9, 1))
    first.end_date = "2026-09-30"
    db_session.commit()
    with pytest.raises(ValueError, match="重叠"):
        assign_teacher(db_session, "S1", "T1", "primary", date(2026, 9, 30))

    second = assign_teacher(db_session, "S1", "T1", "subject", date(2026, 10, 1))
    with pytest.raises(ValueError, match="结束日期"):
        revoke_teacher_assignment(db_session, second.assignment_id, date(2026, 9, 30))


def test_revoked_assignment_history_still_prevents_overlapping_interval(db_session):
    teacher = _teacher(db_session, "T1")
    student = _student(db_session, "S1")
    original = assign_teacher(
        db_session, student.student_id, teacher.teacher_id, "primary", date(2026, 9, 1)
    )
    revoke_teacher_assignment(db_session, original.assignment_id, date(2026, 9, 30))

    for overlapping_start in (date(2026, 9, 15), date(2026, 9, 30)):
        with pytest.raises(ValueError, match="重叠"):
            assign_teacher(
                db_session, student.student_id, teacher.teacher_id,
                "primary", overlapping_start,
            )

    replacement = assign_teacher(
        db_session, student.student_id, teacher.teacher_id, "primary", date(2026, 10, 1)
    )
    assert replacement.start_date == "2026-10-01"


def test_guardian_permissions_require_active_rows_and_return_sorted_unique_students(db_session):
    guardian = Guardian(name="家长", relationship_type="father", status="active")
    db_session.add(guardian)
    students = [
        _student(db_session, "S2", "安安"),
        _student(db_session, "S1", "安安"),
        _student(db_session, "S3", "周周", status="inactive"),
    ]
    db_session.add_all(
        [StudentGuardian(student_id=s.student_id, guardian_id=guardian.guardian_id) for s in students]
    )
    db_session.commit()

    assert guardian_can_access_student(db_session, guardian.guardian_id, "S1")
    assert not guardian_can_access_student(db_session, guardian.guardian_id, "S3")
    assert [s.student_id for s in list_students_for_guardian(db_session, guardian.guardian_id)] == [
        "S1", "S2"
    ]

    guardian.status = "inactive"
    db_session.commit()
    assert not guardian_can_access_student(db_session, guardian.guardian_id, "S1")
    assert list_students_for_guardian(db_session, guardian.guardian_id) == []
