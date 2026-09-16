from datetime import date

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.wecom.scope import resolve_scope


def _add_scope(db, *, teacher_id="T1", student_id="S1", student_name="张三"):
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        teacher = Teacher(
            teacher_id=teacher_id, name=f"{teacher_id}老师", role="晚辅教师", status="active"
        )
        db.add(teacher)
        db.commit()
    klass = Class(
        class_id=f"C-{student_id}", name=f"三年级{student_id}班", grade="三年级",
        class_type="daily", head_teacher_id=teacher_id, status="active",
    )
    student = Student(
        student_id=student_id, name=student_name, grade="三年级", status="active"
    )
    db.add_all([klass, student])
    db.commit()
    db.add(
        Enrollment(
            student_id=student_id, class_id=klass.class_id,
            start_date="2026-01-01", status="active",
        )
    )
    db.commit()
    return teacher, klass, student


def test_resolves_authorized_student_and_defaults_to_28_days(db_session):
    teacher, klass, student = _add_scope(db_session)

    result = resolve_scope(
        db_session, teacher, "请分析张三最近的表现", today=date(2026, 9, 10)
    )

    assert result.status == "resolved"
    assert result.scope.student_id == student.student_id
    assert result.scope.class_id == klass.class_id
    assert result.scope.date_from == "2026-08-14"
    assert result.scope.date_to == "2026-09-10"


def test_does_not_match_student_outside_teacher_permissions(db_session):
    teacher, _, _ = _add_scope(db_session)
    _add_scope(db_session, teacher_id="T2", student_id="S2", student_name="李四")

    result = resolve_scope(db_session, teacher, "分析李四", today=date(2026, 9, 10))

    assert result.status == "scope_required"


def test_duplicate_student_name_requires_choice(db_session):
    teacher, _, _ = _add_scope(db_session, student_id="S1", student_name="张三")
    _add_scope(db_session, student_id="S2", student_name="张三")

    result = resolve_scope(db_session, teacher, "分析张三", today=date(2026, 9, 10))

    assert result.status == "scope_ambiguous"
    assert {item.student_id for item in result.candidates} == {"S1", "S2"}


def test_follow_up_without_name_requires_scope(db_session):
    teacher, _, _ = _add_scope(db_session)

    result = resolve_scope(db_session, teacher, "那学习习惯呢？", today=date(2026, 9, 10))

    assert result.status == "scope_required"
