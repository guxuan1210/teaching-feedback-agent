from datetime import date

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat.models import ChatMessage
from app.wecom.binding import create_binding_code
from app.wecom.handler import process_scope_choice, process_text
from app.wecom.models import TeacherWecomBinding, WecomChatState, WecomInboundMessage


class Provider:
    model = "fake-model"

    def stream(self, messages):
        yield "进步明显"
        yield "，继续保持。"


def _teacher_scope(db):
    teacher = Teacher(teacher_id="T-W", name="王老师", role="晚辅教师", status="active")
    db.add(teacher)
    db.commit()
    klass = Class(
        class_id="C-W", name="三年级A班", grade="三年级", class_type="daily",
        head_teacher_id="T-W", status="active",
    )
    db.add(klass)
    db.commit()
    student = Student(student_id="S-W", name="张三", grade="三年级", status="active")
    db.add(student)
    db.commit()
    db.add(Enrollment(student_id="S-W", class_id="C-W", start_date="2026-01-01", status="active"))
    db.commit()
    return teacher


def test_unbound_user_gets_binding_instructions(db_session):
    events = list(
        process_text(
            db_session, Provider(), "secret", "m1", "u1", "分析张三",
            today=date(2026, 9, 10),
        )
    )
    assert events[-1].status == "binding_required"
    assert "绑定码" in events[-1].content


def test_binding_command_binds_user(db_session):
    teacher = _teacher_scope(db_session)
    code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")

    events = list(
        process_text(db_session, Provider(), "secret", "m1", "u1", f"绑定 {code}")
    )

    assert events[-1].status == "completed"
    assert "王老师" in events[-1].content
    assert db_session.get(TeacherWecomBinding, "u1") is not None


def test_private_question_streams_answer_and_reuses_conversation(db_session):
    teacher = _teacher_scope(db_session)
    db_session.add(TeacherWecomBinding(wecom_user_id="u1", teacher_id=teacher.teacher_id))
    db_session.commit()

    first = list(
        process_text(
            db_session, Provider(), "secret", "m1", "u1", "分析张三最近表现",
            today=date(2026, 9, 10), public_base_url="https://teaching.example.com",
        )
    )
    state = db_session.get(WecomChatState, "u1")
    first_conversation_id = state.conversation_id
    second = list(
        process_text(
            db_session, Provider(), "secret", "m2", "u1", "那学习习惯呢？",
            today=date(2026, 9, 10), public_base_url="https://teaching.example.com",
        )
    )

    assert first[0].status == "generating"
    assert "张三｜2026-08-14 至 2026-09-10" in first[0].content
    assert first[-1].status == "completed"
    assert "查看依据" in first[-1].content
    assert second[-1].status == "completed"
    assert db_session.get(WecomChatState, "u1").conversation_id == first_conversation_id


def test_duplicate_message_is_not_processed_twice(db_session):
    teacher = _teacher_scope(db_session)
    db_session.add(TeacherWecomBinding(wecom_user_id="u1", teacher_id=teacher.teacher_id))
    db_session.commit()
    list(process_text(db_session, Provider(), "secret", "same", "u1", "分析张三"))

    repeated = list(
        process_text(db_session, Provider(), "secret", "same", "u1", "分析张三")
    )

    assert repeated == []
    assert db_session.get(WecomInboundMessage, "same") is not None


def test_group_chat_is_rejected_without_reading_student_data(db_session):
    events = list(
        process_text(
            db_session, Provider(), "secret", "m1", "u1", "分析张三", chattype="group"
        )
    )
    assert events[-1].status == "failed"
    assert "仅支持老师私聊" in events[-1].content


def test_unsupported_scope_prompts_for_student_or_class(db_session):
    teacher = _teacher_scope(db_session)
    db_session.add(TeacherWecomBinding(wecom_user_id="u1", teacher_id=teacher.teacher_id))
    db_session.commit()

    events = list(
        process_text(db_session, Provider(), "secret", "m1", "u1", "帮我分析一下")
    )

    assert events[-1].status == "scope_required"
    assert "学生或班级" in events[-1].content


def test_five_wrong_binding_codes_temporarily_lock_user(db_session):
    teacher = _teacher_scope(db_session)
    valid_code, _ = create_binding_code(db_session, teacher.teacher_id, "secret")
    wrong_code = "000000" if valid_code != "000000" else "000001"
    for index in range(5):
        list(
            process_text(
                db_session, Provider(), "secret", f"bad-{index}", "u1", f"绑定 {wrong_code}"
            )
        )

    blocked = list(
        process_text(
            db_session, Provider(), "secret", "valid", "u1", f"绑定 {valid_code}"
        )
    )

    assert blocked[-1].status == "failed"
    assert "10分钟后" in blocked[-1].content
    assert db_session.get(TeacherWecomBinding, "u1") is None


def test_scope_choice_continues_original_question(db_session):
    teacher = _teacher_scope(db_session)
    second_class = Class(
        class_id="C-W2", name="三年级B班", grade="三年级", class_type="daily",
        head_teacher_id=teacher.teacher_id, status="active",
    )
    second_student = Student(student_id="S-W2", name="张三", grade="三年级", status="active")
    db_session.add(second_class)
    db_session.commit()
    db_session.add(second_student)
    db_session.commit()
    db_session.add(
        Enrollment(student_id="S-W2", class_id="C-W2", start_date="2026-01-01", status="active")
    )
    db_session.add(TeacherWecomBinding(wecom_user_id="u1", teacher_id=teacher.teacher_id))
    db_session.commit()
    ambiguous = list(
        process_text(
            db_session, Provider(), "secret", "m1", "u1", "分析张三最近表现",
            today=date(2026, 9, 10),
        )
    )
    assert ambiguous[-1].status == "scope_ambiguous"

    answered = list(
        process_scope_choice(
            db_session, Provider(), "secret", "choice-1", "u1", 1,
            today=date(2026, 9, 10),
        )
    )

    assert answered[-1].status == "completed"
    assert db_session.get(WecomChatState, "u1").student_id == "S-W2"
    user_messages = [
        row.content for row in db_session.query(ChatMessage).filter(ChatMessage.role == "user")
    ]
    assert user_messages == ["分析张三最近表现"]
