from datetime import date

import pytest
from sqlalchemy import select

from app.catalog.models import Enrollment
from app.catalog.repository import active_roster, enroll_student, leave_class


def test_student_can_rejoin_same_class_after_leaving(db_session, student, classroom):
    first = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))
    leave_class(db_session, first.enrollment_id, date(2026, 9, 10))
    second = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 20))
    assert second.enrollment_id != first.enrollment_id


def test_overlapping_enrollment_is_rejected(db_session, student, classroom):
    enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))
    with pytest.raises(ValueError, match="已在该班级的有效期内"):
        enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 5))


def test_inactive_student_or_class_is_rejected(db_session, student, classroom):
    student.status = "inactive"
    db_session.commit()
    with pytest.raises(ValueError, match="学生已停用"):
        enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))

    student.status = "active"
    classroom.status = "inactive"
    db_session.commit()
    with pytest.raises(ValueError, match="班级已停用"):
        enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))


def test_invalid_start_date_via_http_returns_422(client, student, classroom):
    response = client.post("/catalog/enrollments", data={
        "student_id": student.student_id, "class_id": classroom.class_id,
        "start_date": "not-a-date",
    })
    assert response.status_code == 422


def test_leave_rejects_end_date_before_start(db_session, student, classroom):
    enrollment = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 10))
    with pytest.raises(ValueError, match="离班日期不能早于入班日期"):
        leave_class(db_session, enrollment.enrollment_id, date(2026, 9, 1))


def test_active_roster_reflects_effective_dates(db_session, student, classroom):
    enrollment = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))

    assert [s.student_id for s in active_roster(db_session, classroom.class_id, date(2026, 8, 31))] == []
    assert [s.student_id for s in active_roster(db_session, classroom.class_id, date(2026, 9, 1))] == [student.student_id]

    leave_class(db_session, enrollment.enrollment_id, date(2026, 9, 10))
    assert [s.student_id for s in active_roster(db_session, classroom.class_id, date(2026, 9, 11))] == []
    assert [s.student_id for s in active_roster(db_session, classroom.class_id, date(2026, 9, 5))] == [student.student_id]


def test_enroll_and_leave_via_http(client, db_session, student, classroom):
    response = client.post("/catalog/enrollments", data={
        "student_id": student.student_id, "class_id": classroom.class_id,
        "start_date": "2026-09-01",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert student.name in response.text
    assert "在班" in response.text

    enrollment = db_session.scalar(
        select(Enrollment).where(Enrollment.student_id == student.student_id)
    )
    leave = client.post(f"/catalog/enrollments/{enrollment.enrollment_id}/leave", data={
        "end_date": "2026-09-10",
    }, follow_redirects=True)
    assert leave.status_code == 200
    assert "已离班" in leave.text
