"""Chat service: permissions, context building, citations, and retry rules."""

from __future__ import annotations

import pytest

from sqlalchemy import select

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat import service
from app.chat.context import ChatSource
from app.chat.models import ChatConversation, ChatMessage


def _seed(db, role="晚辅教师", teacher_id="T1"):
    teacher = Teacher(teacher_id=teacher_id, name="王老师", role=role, status="active")
    db.add(teacher)
    db.commit()
    klass = Class(
        class_id="C1", name="三年级A班", grade="三年级",
        class_type="daily", head_teacher_id=teacher_id, status="active",
    )
    db.add(klass)
    db.commit()
    return teacher, klass


def _enroll(db, student_id, class_id="C1"):
    student = Student(
        student_id=student_id, name="李明", grade="三年级",
        current_stage="三阶", status="active",
    )
    db.add(student)
    db.commit()
    db.add(
        Enrollment(
            student_id=student_id, class_id=class_id,
            start_date="2026-09-01", status="active",
        )
    )
    db.commit()
    return student


def _student_conversation(db, teacher):
    _enroll(db, "S1")
    return service.create_conversation(
        db, teacher, scope_type="student", class_id="C1", student_id="S1",
        date_from="2026-09-01", date_to="2026-09-08",
    )


def test_create_conversation_requires_enrollment(db_session):
    teacher, _ = _seed(db_session)
    with pytest.raises(service.ChatError) as exc:
        service.create_conversation(
            db_session, teacher, scope_type="student", class_id="C1",
            student_id="S1", date_from="2026-09-01", date_to="2026-09-08",
        )
    assert exc.value.status_code == 422


def test_create_conversation_forbidden_for_non_head(db_session):
    _seed(db_session, teacher_id="T1")
    other = Teacher(teacher_id="T2", name="别的老师", role="晚辅教师", status="active")
    db_session.add(other)
    db_session.commit()
    with pytest.raises(service.ChatError) as exc:
        service.create_conversation(
            db_session, other, scope_type="class", class_id="C1",
            student_id=None, date_from="2026-09-01", date_to="2026-09-08",
        )
    assert exc.value.status_code == 403


def test_create_class_conversation_rejects_over_40_students(db_session):
    teacher, _ = _seed(db_session)
    for index in range(41):
        _enroll(db_session, f"S{index}")
    with pytest.raises(service.ChatError) as exc:
        service.create_conversation(
            db_session, teacher, scope_type="class", class_id="C1",
            student_id=None, date_from="2026-09-01", date_to="2026-09-08",
        )
    assert "40" in str(exc.value)
    assert exc.value.status_code == 422


def test_prepare_send_saves_user_message(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    prepared = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "总结一下", "fake-model", channel="web"
    )
    assert prepared.user_message.role == "user"
    assert prepared.user_message.content == "总结一下"
    assert prepared.user_message.status == "completed"


def test_messages_inherit_user_channel(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)

    ok = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "总结", "fake-model", channel="wecom"
    )
    assert ok.user_message.channel == "wecom"
    saved = service.save_assistant_message(db_session, ok, "回答", set())
    assert saved.channel == "wecom"

    bad = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "再问", "fake-model", channel="web"
    )
    failed = service.save_failed_message(db_session, bad, "部分", "模型服务中断")
    assert bad.user_message.channel == "web"
    assert failed.channel == "web"


def test_parse_citations_filters_invalid_numbers():
    sources = [
        ChatSource("daily", "F1", "x", "/x"),
        ChatSource("daily", "F2", "y", "/y"),
    ]
    assert service.parse_citations("看 [S1] 和 [S2] 以及 [S99]", sources) == {1, 2}
    assert service.parse_citations("没有引用标记", sources) == set()
    assert service.parse_citations("引用 [S0]", sources) == set()


def test_sources_payload_puts_cited_first():
    sources = [
        ChatSource("daily", "F1", "一号", "/a"),
        ChatSource("daily", "F2", "二号", "/b"),
    ]
    payload = service.sources_payload(sources, {2})
    assert [item["id"] for item in payload] == ["F2", "F1"]
    assert payload[0]["cited"] is True
    assert payload[1]["cited"] is False


def test_retry_reuses_user_message(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    prepared = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "再试一次", "fake-model", channel="web"
    )
    failed_id = prepared.assistant_message_id
    service.save_failed_message(db_session, prepared, "部分", "模型服务中断")

    retried = service.prepare_retry(
        db_session, teacher, conversation.conversation_id, failed_id, "fake-model"
    )
    assert retried.user_message.content == "再试一次"

    service.save_assistant_message(db_session, retried, "完整回答", {1})


def test_retry_blocked_after_later_success(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    first = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "第一个问题", "fake-model", channel="web"
    )
    service.save_failed_message(db_session, first, "部分", "模型服务中断")

    second = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "第二个问题", "fake-model", channel="web"
    )
    service.save_assistant_message(db_session, second, "成功回答", set())

    with pytest.raises(service.ChatError) as exc:
        service.prepare_retry(
            db_session, teacher, conversation.conversation_id,
            first.assistant_message_id, "fake-model",
        )
    assert exc.value.status_code == 409


def test_archive_blocks_send(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    service.archive_conversation(db_session, teacher, conversation.conversation_id)
    with pytest.raises(service.ChatError) as exc:
        service.prepare_send(
            db_session, teacher, conversation.conversation_id, "再发一条", "fake-model", channel="web"
        )
    assert exc.value.status_code == 403


def test_delete_conversation_cascades_to_messages(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    prepared = service.prepare_send(
        db_session, teacher, conversation.conversation_id, "总结一下", "fake-model", channel="web"
    )
    service.save_assistant_message(db_session, prepared, "回答", set())

    service.delete_conversation(db_session, teacher, conversation.conversation_id)

    assert db_session.get(ChatConversation, conversation.conversation_id) is None
    remaining = db_session.scalars(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.conversation_id
        )
    ).all()
    assert remaining == []


def test_delete_conversation_forbidden_for_non_owner(db_session):
    teacher, _ = _seed(db_session)
    conversation = _student_conversation(db_session, teacher)
    other = Teacher(teacher_id="T2", name="别的老师", role="晚辅教师", status="active")
    db_session.add(other)
    db_session.commit()

    with pytest.raises(service.ChatError) as exc:
        service.delete_conversation(db_session, other, conversation.conversation_id)
    assert exc.value.status_code == 403
