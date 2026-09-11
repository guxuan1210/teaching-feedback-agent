"""Chat schema constraints and cascade behavior."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat.models import ChatConversation, ChatMessage, ChatMessageSource
from app.core.ids import new_id


def _seed_scope(db_session):
    teacher = Teacher(teacher_id="T1", name="王老师", role="晚辅教师", status="active")
    db_session.add(teacher)
    db_session.commit()
    klass = Class(
        class_id="C1", name="三年级A班", grade="三年级",
        class_type="daily", head_teacher_id="T1", status="active",
    )
    db_session.add(klass)
    db_session.commit()
    student = Student(
        student_id="S1", name="李明", grade="三年级", current_stage="三阶", status="active"
    )
    db_session.add(student)
    db_session.commit()
    db_session.add(
        Enrollment(student_id="S1", class_id="C1", start_date="2026-09-01", status="active")
    )
    db_session.commit()
    return teacher, klass, student


def _conversation(**overrides):
    defaults = dict(
        conversation_id=new_id("CHAT"),
        owner_teacher_id="T1",
        scope_type="student",
        class_id="C1",
        student_id="S1",
        title="李明教学分析",
        date_from="2026-09-01",
        date_to="2026-09-08",
        status="active",
    )
    defaults.update(overrides)
    return ChatConversation(**defaults)


def test_student_conversation_persists(db_session):
    _seed_scope(db_session)
    conversation = _conversation()
    db_session.add(conversation)
    db_session.commit()
    assert db_session.get(ChatConversation, conversation.conversation_id) is not None


def test_class_conversation_has_null_student(db_session):
    _seed_scope(db_session)
    conversation = _conversation(scope_type="class", student_id=None, title="三年级A班教学分析")
    db_session.add(conversation)
    db_session.commit()
    assert conversation.student_id is None


def test_student_scope_requires_student(db_session):
    _seed_scope(db_session)
    conversation = _conversation(scope_type="student", student_id=None)
    db_session.add(conversation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_date_range_constraint(db_session):
    _seed_scope(db_session)
    conversation = _conversation(date_from="2026-09-08", date_to="2026-09-01")
    db_session.add(conversation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_message_and_source_cascade(db_session):
    _seed_scope(db_session)
    conversation = _conversation()
    db_session.add(conversation)
    db_session.commit()

    message = ChatMessage(
        message_id="MSG1",
        conversation_id=conversation.conversation_id,
        role="assistant",
        content="回答",
        status="completed",
    )
    db_session.add(message)
    db_session.commit()
    db_session.add(
        ChatMessageSource(
            message_id="MSG1", source_type="daily", source_id="F1", cited=1
        )
    )
    db_session.commit()

    db_session.delete(conversation)
    db_session.commit()

    assert db_session.get(ChatMessage, "MSG1") is None
    assert db_session.scalars(
        select(ChatMessageSource).where(ChatMessageSource.message_id == "MSG1")
    ).first() is None
