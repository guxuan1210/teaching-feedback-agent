from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat import service
from app.chat.generation import stream_generation
from app.chat.models import ChatMessage


class Provider:
    model = "fake-model"

    def __init__(self, chunks=("第一段", "第二段"), error=None):
        self.chunks = chunks
        self.error = error

    def stream(self, messages):
        yield from self.chunks
        if self.error:
            raise self.error


def _prepared(db):
    teacher = Teacher(teacher_id="T-G", name="王老师", role="晚辅教师", status="active")
    klass = Class(
        class_id="C-G", name="三年级A班", grade="三年级", class_type="daily",
        head_teacher_id="T-G", status="active",
    )
    student = Student(student_id="S-G", name="张三", grade="三年级", status="active")
    db.add(teacher)
    db.commit()
    db.add(klass)
    db.commit()
    db.add(student)
    db.commit()
    db.add(Enrollment(student_id="S-G", class_id="C-G", start_date="2026-01-01", status="active"))
    db.commit()
    conversation = service.create_conversation(
        db, teacher, scope_type="student", class_id="C-G", student_id="S-G",
        date_from="2026-08-14", date_to="2026-09-10",
    )
    return service.prepare_send(
        db, teacher, conversation.conversation_id, "总结表现", "fake-model"
    )


def test_generation_emits_common_events_and_saves_answer(db_session):
    prepared = _prepared(db_session)

    events = list(stream_generation(db_session, prepared, Provider()))

    assert [event.name for event in events] == ["meta", "delta", "delta", "sources", "done"]
    saved = db_session.get(ChatMessage, prepared.assistant_message_id)
    assert saved.content == "第一段第二段"
    assert saved.status == "completed"


def test_generation_saves_partial_failure_and_emits_error(db_session):
    prepared = _prepared(db_session)

    events = list(
        stream_generation(db_session, prepared, Provider(("部分",), RuntimeError("down")))
    )

    assert events[-1].name == "error"
    assert events[-1].payload["retryable"] is True
    saved = db_session.get(ChatMessage, prepared.assistant_message_id)
    assert saved.content == "部分"
    assert saved.status == "failed"


def test_generation_stops_at_timeout(db_session):
    prepared = _prepared(db_session)
    ticks = iter((0.0, 1.0, 200.0))

    events = list(
        stream_generation(
            db_session, prepared, Provider(("第一段", "第二段")),
            timeout_seconds=165, clock=lambda: next(ticks),
        )
    )

    assert events[-1].name == "error"
    assert events[-1].payload["message"] == "生成超时，请发送“重试”"
    saved = db_session.get(ChatMessage, prepared.assistant_message_id)
    assert saved.status == "failed"
