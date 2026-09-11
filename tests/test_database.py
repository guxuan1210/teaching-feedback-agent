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
    assert "共 22 张表" in schema
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
        "teacher_wecom_binding",
        "wecom_binding_code",
        "wecom_chat_state",
        "wecom_inbound_message",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
