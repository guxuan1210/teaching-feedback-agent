"""Unified current conversation: lifecycle, invalidation, and permission loss."""

from __future__ import annotations

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat import service
from app.chat.models import TeacherChatState


def _seed(db):
    teacher = Teacher(teacher_id="T1", name="王老师", role="晚辅教师", status="active")
    db.add(teacher)
    db.commit()
    klass = Class(
        class_id="C1", name="三年级A班", grade="三年级", class_type="daily",
        head_teacher_id="T1", status="active",
    )
    db.add(klass)
    db.commit()
    student = Student(student_id="S1", name="张三", grade="三年级", status="active")
    db.add(student)
    db.commit()
    db.add(
        Enrollment(
            student_id="S1", class_id="C1", start_date="2026-01-01", status="active"
        )
    )
    db.commit()
    return teacher


def _conversation(db, teacher):
    return service.create_conversation(
        db, teacher, scope_type="student", class_id="C1", student_id="S1",
        date_from="2026-08-14", date_to="2026-09-10",
    )


def test_get_current_conversation_returns_none_when_unset(db_session):
    teacher = _seed(db_session)
    assert service.get_current_conversation(db_session, teacher) is None


def test_mark_then_get_returns_same_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    current = service.get_current_conversation(db_session, teacher)
    assert current is not None
    assert current.conversation_id == conversation.conversation_id


def test_clear_current_conversation_removes_state(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    service.clear_current_conversation(db_session, teacher.teacher_id)

    assert db_session.get(TeacherChatState, teacher.teacher_id) is None


def test_clear_with_mismatched_id_is_noop(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    service.clear_current_conversation(db_session, teacher.teacher_id, "OTHER")

    assert service.get_current_conversation(db_session, teacher) is not None


def test_archive_clears_current_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    service.archive_conversation(db_session, teacher, conversation.conversation_id)

    assert service.get_current_conversation(db_session, teacher) is None


def test_delete_clears_current_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    service.delete_conversation(db_session, teacher, conversation.conversation_id)

    assert service.get_current_conversation(db_session, teacher) is None


def test_inactive_student_invalidates_current_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    student = db_session.get(Student, "S1")
    student.status = "inactive"
    db_session.commit()

    assert service.get_current_conversation(db_session, teacher) is None
    assert db_session.get(TeacherChatState, teacher.teacher_id) is None


def test_inactive_class_invalidates_current_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    klass = db_session.get(Class, "C1")
    klass.status = "inactive"
    db_session.commit()

    assert service.get_current_conversation(db_session, teacher) is None


def test_permission_loss_invalidates_current_conversation(db_session):
    teacher = _seed(db_session)
    conversation = _conversation(db_session, teacher)
    service.mark_current_conversation(db_session, teacher, conversation)

    other = Teacher(teacher_id="T2", name="别的老师", role="晚辅教师", status="active")
    db_session.add(other)
    db_session.commit()
    klass = db_session.get(Class, "C1")
    klass.head_teacher_id = "T2"
    db_session.commit()

    assert service.get_current_conversation(db_session, teacher) is None
    assert db_session.get(TeacherChatState, teacher.teacher_id) is None
