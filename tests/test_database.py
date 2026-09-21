import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.catalog.models import Student
from app.core.database import (
    SCHEMA_VERSION,
    _compose_legacy_parent_message,
    build_engine,
    initialize_database,
)
from app.feedback.models import DailyFeedback


def test_foreign_keys_are_enabled(db_session):
    enabled = db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    assert enabled == 1


def test_schema_version_is_nine():
    assert SCHEMA_VERSION == 9


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
    assert "共 34 张表" in schema
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
        "guardian",
        "student_guardian",
        "guardian_channel_binding",
        "guardian_invitation",
        "student_teacher_assignment",
        "student_image",
        "pending_media_assignment",
        "family_conversation",
        "family_message",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema


def test_schema_sql_executes_and_exposes_family_structure(tmp_path):
    path = tmp_path / "schema.db"
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    connection.executescript(schema)
    connection.close()

    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    inspector = inspect(engine)
    assert len(inspector.get_table_names()) == 34
    binding_columns = {
        column["name"] for column in inspector.get_columns("guardian_channel_binding")
    }
    assert {"active_student_id", "pending_state_json"} <= binding_columns
    assignment_columns = {column["name"] for column in inspector.get_columns("student_teacher_assignment")}
    assert {"origin", "source_enrollment_id"} <= assignment_columns
    assignment_foreign_keys = {
        (tuple(item["constrained_columns"]), item["referred_table"])
        for item in inspector.get_foreign_keys("student_teacher_assignment")
    }
    assert (("source_enrollment_id",), "enrollment") in assignment_foreign_keys
    invitation_defaults = {column["name"]: column["default"] for column in inspector.get_columns("guardian_invitation")}
    assert invitation_defaults["max_uses"] == "1"
    assert {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("student_image")
    } >= {("source_message_id", "source_position")}
    image_foreign_keys = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("student_image")
    }
    assert (("student_id",), "student", None) in image_foreign_keys
    assert (("deleted_by_teacher_id",), "teacher", None) in image_foreign_keys
    message_checks = " ".join(
        item["sqltext"] for item in inspector.get_check_constraints("family_message")
    )
    assert "direction = 'inbound' AND sender_type = 'guardian'" in message_checks
    message_indexes = {
        tuple(item["column_names"]) for item in inspector.get_indexes("family_message")
    }
    assert ("conversation_id", "created_at") in message_indexes
    engine.dispose()


def test_v7_database_upgrades_to_v9_with_family_tables(tmp_path):
    path = tmp_path / "v7.db"
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    legacy_schema = schema.split("CREATE TABLE IF NOT EXISTS guardian (", 1)[0]
    connection = sqlite3.connect(path)
    connection.executescript(legacy_schema)
    connection.execute("PRAGMA user_version = 7")
    connection.close()

    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    initialize_database(engine)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "guardian",
        "student_guardian",
        "guardian_channel_binding",
        "guardian_invitation",
        "student_teacher_assignment",
        "student_image",
        "pending_media_assignment",
        "family_conversation",
        "family_message",
    }
    with engine.connect() as upgraded:
        assert upgraded.exec_driver_sql("PRAGMA user_version").scalar_one() == 9
    engine.dispose()


def test_v8_family_database_migrates_assignments_without_data_loss(tmp_path):
    path = tmp_path / "v8-family.db"
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    old_schema = schema.replace(
        "    max_uses             INTEGER NOT NULL DEFAULT 1,\n",
        "    max_uses             INTEGER NOT NULL,\n",
    ).replace(
        "    origin        TEXT NOT NULL DEFAULT 'manual',\n"
        "    source_enrollment_id INTEGER REFERENCES enrollment(enrollment_id),\n",
        "",
    ).replace(
        "    CHECK (status IN ('active', 'revoked')),\n"
        "    CHECK ((origin = 'manual' AND source_enrollment_id IS NULL)\n"
        "        OR (origin = 'class_sync' AND source_enrollment_id IS NOT NULL AND role = 'primary'))\n",
        "    CHECK (status IN ('active', 'revoked'))\n",
    ).replace(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_student_teacher_active_role\n"
        "    ON student_teacher_assignment(student_id, teacher_id, role) WHERE status = 'active';\n",
        "",
    )
    connection = sqlite3.connect(path)
    connection.executescript(old_schema)
    connection.execute(
        "INSERT INTO teacher (teacher_id,name,role,status) VALUES ('T-V8','旧教师','晚辅教师','active')"
    )
    connection.execute(
        "INSERT INTO student (student_id,name,status) VALUES ('S-V8','旧学生','active')"
    )
    connection.execute(
        "INSERT INTO student_teacher_assignment "
        "(assignment_id,student_id,teacher_id,role,start_date,status) "
        "VALUES ('STA-V8-A','S-V8','T-V8','primary','2026-09-01','active')"
    )
    connection.execute(
        "INSERT INTO student_teacher_assignment "
        "(assignment_id,student_id,teacher_id,role,start_date,status) "
        "VALUES ('STA-V8-B','S-V8','T-V8','primary','2026-09-02','active')"
    )
    connection.execute("PRAGMA user_version = 8")
    connection.commit()
    connection.close()

    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    initialize_database(engine)
    inspector = inspect(engine)
    assert {"origin", "source_enrollment_id"} <= {
        column["name"] for column in inspector.get_columns("student_teacher_assignment")
    }
    invitation_columns = {
        column["name"]: column
        for column in inspector.get_columns("guardian_invitation")
    }
    assert invitation_columns["max_uses"]["default"] is None
    assert any(
        tuple(item["constrained_columns"]) == ("source_enrollment_id",)
        and item["referred_table"] == "enrollment"
        for item in inspector.get_foreign_keys("student_teacher_assignment")
    )
    assert any(
        item["name"] == "uq_student_teacher_active_role" and item["unique"]
        for item in inspector.get_indexes("student_teacher_assignment")
    )
    assignment_checks = " ".join(
        item["sqltext"]
        for item in inspector.get_check_constraints("student_teacher_assignment")
    )
    assert "origin = 'manual'" in assignment_checks
    assert "origin = 'class_sync'" in assignment_checks
    with engine.begin() as upgraded:
        assert upgraded.exec_driver_sql("PRAGMA user_version").scalar_one() == 9
        rows = upgraded.exec_driver_sql(
            "SELECT assignment_id,start_date,end_date,status,origin,source_enrollment_id "
            "FROM student_teacher_assignment ORDER BY assignment_id"
        ).all()
        assert [row.assignment_id for row in rows] == ["STA-V8-A", "STA-V8-B"]
        assert sum(row.status == "active" for row in rows) == 1
        assert all(row.origin == "manual" and row.source_enrollment_id is None for row in rows)
        assert next(row for row in rows if row.status == "active").assignment_id == "STA-V8-A"
        revoked = next(row for row in rows if row.status == "revoked")
        assert revoked.end_date == max(
            revoked.start_date, datetime.now(timezone.utc).date().isoformat()
        )
        assert upgraded.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with pytest.raises(IntegrityError):
            upgraded.exec_driver_sql(
                "INSERT INTO student_teacher_assignment "
                "(assignment_id,student_id,teacher_id,role,start_date,status) "
                "VALUES ('STA-DUP','S-V8','T-V8','primary','2026-09-02','active')"
            )
    with engine.begin() as upgraded:
        with pytest.raises(IntegrityError):
            upgraded.exec_driver_sql(
                "INSERT INTO student_teacher_assignment "
                "(assignment_id,student_id,teacher_id,role,start_date,status,origin) "
                "VALUES ('STA-BAD','S-V8','T-V8','subject','2026-09-02','revoked','bogus')"
            )
    engine.dispose()
