from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.catalog.models import Student
from app.core.database import (
    _compose_legacy_parent_message,
    build_engine,
    initialize_database,
)
from app.feedback.models import DailyFeedback


def test_foreign_keys_are_enabled(db_session):
    enabled = db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    assert enabled == 1


def test_compose_legacy_parent_message_uses_deterministic_format():
    message = _compose_legacy_parent_message(
        "总结文本", '["优势A", "优势B"]', '["待关注A"]', '["建议A"]'
    )
    assert message == "总结文本\n\n优势：优势A；优势B\n\n待关注：待关注A\n\n建议：建议A"


def test_legacy_report_backfills_parent_message(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with legacy.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE weekly_report ("
            "report_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, "
            "class_id TEXT NOT NULL, teacher_id TEXT NOT NULL, "
            "period_start TEXT NOT NULL, period_end TEXT NOT NULL, "
            "generation_mode TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'draft', "
            "summary TEXT NOT NULL, strengths TEXT NOT NULL, "
            "concerns TEXT NOT NULL, suggestions TEXT NOT NULL, "
            "generation_note TEXT, finalized_at TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO weekly_report VALUES "
            "('R-LEGACY','S1','C1','T1','2026-09-01','2026-09-07','template','draft',"
            "'总结文本','[\"优势A\"]','[\"待关注A\"]','[\"建议A\"]',NULL,NULL,'old','old')"
        )
        connection.exec_driver_sql("PRAGMA user_version = 1")
    legacy.dispose()

    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    initialize_database(engine)
    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(weekly_report)")
        }
        assert "parent_message" in columns
        message = connection.exec_driver_sql(
            "SELECT parent_message FROM weekly_report WHERE report_id='R-LEGACY'"
        ).scalar_one()
        assert message == "总结文本\n\n优势：优势A\n\n待关注：待关注A\n\n建议：建议A"
    engine.dispose()


def test_backfill_teacher_chat_state_promotes_only_valid_conversations(db_session, engine):
    from app.catalog.models import Class, Student, Teacher
    from app.chat.models import ChatConversation, TeacherChatState
    from app.core.database import _backfill_teacher_chat_state
    from app.wecom.models import TeacherWecomBinding, WecomChatState

    teachers = [
        Teacher(teacher_id="T-ACTIVE", name="王老师", role="晚辅教师", status="active"),
        Teacher(teacher_id="T-ARCHIVED", name="李老师", role="晚辅教师", status="active"),
        Teacher(teacher_id="T-FOREIGN", name="赵老师", role="晚辅教师", status="active"),
        Teacher(teacher_id="T-OTHER", name="钱老师", role="晚辅教师", status="active"),
    ]
    db_session.add_all(teachers)
    db_session.commit()
    db_session.add(
        Class(
            class_id="C-BF", name="三年级A班", grade="三年级", class_type="daily",
            head_teacher_id="T-ACTIVE", status="active",
        )
    )
    db_session.commit()
    db_session.add(Student(student_id="S-BF", name="张三", grade="三年级", status="active"))
    db_session.commit()

    def _conversation(cid, owner_id, status):
        return ChatConversation(
            conversation_id=cid, owner_teacher_id=owner_id, scope_type="student",
            class_id="C-BF", student_id="S-BF", title="张三教学分析",
            date_from="2026-09-01", date_to="2026-09-08", status=status,
        )

    db_session.add_all([
        _conversation("CHAT-ACTIVE", "T-ACTIVE", "active"),
        _conversation("CHAT-ARCHIVED", "T-ARCHIVED", "archived"),
        _conversation("CHAT-FOREIGN", "T-OTHER", "active"),
    ])
    db_session.commit()
    db_session.add_all([
        TeacherWecomBinding(wecom_user_id="u-active", teacher_id="T-ACTIVE"),
        TeacherWecomBinding(wecom_user_id="u-archived", teacher_id="T-ARCHIVED"),
        TeacherWecomBinding(wecom_user_id="u-foreign", teacher_id="T-FOREIGN"),
    ])
    db_session.commit()
    db_session.add_all([
        WecomChatState(wecom_user_id="u-active", conversation_id="CHAT-ACTIVE"),
        WecomChatState(wecom_user_id="u-archived", conversation_id="CHAT-ARCHIVED"),
        WecomChatState(wecom_user_id="u-foreign", conversation_id="CHAT-FOREIGN"),
    ])
    db_session.commit()

    _backfill_teacher_chat_state(engine)

    active_state = db_session.get(TeacherChatState, "T-ACTIVE")
    assert active_state is not None
    assert active_state.current_conversation_id == "CHAT-ACTIVE"
    assert db_session.get(TeacherChatState, "T-ARCHIVED") is None
    assert db_session.get(TeacherChatState, "T-FOREIGN") is None
    assert db_session.get(TeacherChatState, "T-OTHER") is None


def test_daily_rating_rejects_values_outside_one_to_five(db_session, daily_session, student):
    row = DailyFeedback(
        feedback_id="F-invalid", session_id=daily_session.session_id,
        student_id=student.student_id, rating_knowledge=0,
        rating_habit=3, rating_mindset=3,
    )
    db_session.add(row)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_schema_sql_mirrors_current_model_fields():
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    assert "password_hash" in schema
    assert "late_care_level TEXT" in schema
    assert "parent_message" in schema
    assert "generation_note" in schema
    assert "finalized_at" in schema
    assert "共 23 张表" in schema
    assert "channel" in schema
    for table in (
        "stage_dict",
        "late_care_level_dict",
        "assessment_module",
        "assessment_dimension",
        "assessment_score_anchor",
        "weekly_report",
        "weekly_report_source",
        "chat_conversation",
        "chat_message",
        "chat_message_source",
        "teacher_chat_state",
        "teacher_wecom_binding",
        "wecom_binding_code",
        "wecom_chat_state",
        "wecom_inbound_message",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
