from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.catalog.models import Teacher
from app.wecom.binding import bind_user, create_binding_code
from app.wecom.models import TeacherWecomBinding


def test_valid_code_binds_active_teacher_once(db_session, teacher):
    code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")

    outcome = bind_user(db_session, "zhangsan", code, "secret")

    assert outcome.status == "completed"
    assert outcome.teacher_name == teacher.name
    row = db_session.get(TeacherWecomBinding, "zhangsan")
    assert row.teacher_id == teacher.teacher_id
    assert bind_user(db_session, "lisi", code, "secret").status == "invalid_code"


def test_expired_code_is_rejected(db_session, teacher):
    code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")
    future = datetime.now(timezone.utc) + timedelta(minutes=11)

    outcome = bind_user(db_session, "zhangsan", code, "secret", now=future)

    assert outcome.status == "expired_code"
    assert db_session.get(TeacherWecomBinding, "zhangsan") is None


def test_inactive_teacher_cannot_be_bound(db_session, teacher):
    code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")
    teacher.status = "inactive"
    db_session.commit()

    outcome = bind_user(db_session, "zhangsan", code, "secret")

    assert outcome.status == "inactive_teacher"


def test_bound_wecom_user_cannot_switch_teacher(db_session, teacher):
    code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")
    assert bind_user(db_session, "zhangsan", code, "secret").status == "completed"
    other = Teacher(teacher_id="T2", name="另一位教师", role="教师", status="active")
    db_session.add(other)
    db_session.commit()
    other_code, _ = create_binding_code(db_session, other.teacher_id, "secret")

    outcome = bind_user(db_session, "zhangsan", other_code, "secret")

    assert outcome.status == "already_bound"
    binding = db_session.scalar(
        select(TeacherWecomBinding).where(
            TeacherWecomBinding.wecom_user_id == "zhangsan"
        )
    )
    assert binding.teacher_id == teacher.teacher_id
