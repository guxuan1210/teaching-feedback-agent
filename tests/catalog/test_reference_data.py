from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.database import build_engine, initialize_database


def _scalar(db_session, sql: str):
    return db_session.execute(text(sql)).scalar_one()


def test_reference_dictionaries_are_seeded_with_complete_source_data(client, db_session):
    assert _scalar(db_session, "SELECT COUNT(*) FROM stage_dict") == 10
    assert _scalar(db_session, "SELECT COUNT(*) FROM late_care_level_dict") == 6
    assert _scalar(db_session, "SELECT COUNT(*) FROM assessment_module") == 2
    assert _scalar(db_session, "SELECT COUNT(*) FROM assessment_dimension") == 16
    assert _scalar(db_session, "SELECT COUNT(*) FROM assessment_score_anchor") == 96

    assert list(
        db_session.execute(
            text("SELECT stage_id FROM stage_dict ORDER BY sort_order")
        ).scalars()
    ) == ["零阶", "一阶", "二阶", "三阶", "四阶", "五阶", "六阶", "七阶", "八阶", "九阶"]
    assert list(
        db_session.execute(
            text("SELECT level_id FROM late_care_level_dict ORDER BY sort_order")
        ).scalars()
    ) == ["速习蜕变班", "固本A", "固本B", "冲刺A", "冲刺B", "跨学闭环班"]

    dimension_groups = {
        row[0]: row[1]
        for row in db_session.execute(
            text(
                "SELECT module_id, group_concat(dimension_id, ',') "
                "FROM (SELECT module_id, dimension_id FROM assessment_dimension "
                "ORDER BY module_id, sort_order) GROUP BY module_id"
            )
        )
    }
    assert dimension_groups == {
        "habit": "H01,H02,H03,H04,H05,H06,H07,H08",
        "knowledge": "K01,K02,K03,K04,K05,K06,K07,K08",
    }
    anchor_coverage = db_session.execute(
        text(
            "SELECT dimension_id, COUNT(*), MIN(score), MAX(score) "
            "FROM assessment_score_anchor GROUP BY dimension_id"
        )
    ).all()
    assert len(anchor_coverage) == 16
    assert all((count, minimum, maximum) == (6, 0, 5) for _, count, minimum, maximum in anchor_coverage)

    stage = db_session.execute(
        text(
            "SELECT name, summary, goal FROM stage_dict "
            "WHERE stage_id = '三阶'"
        )
    ).one()
    assert stage.name == "学科思维成型告别死记硬背"
    assert stage.summary == "只会机械做题，题目一变就不会？学通学透比刷题量更重要"
    assert stage.goal == "建立严谨审题、速算推导、逻辑思考、灵活运用的学科底层思维"

    anchor = db_session.execute(
        text(
            "SELECT description FROM assessment_score_anchor "
            "WHERE dimension_id = 'K03' AND score = 5"
        )
    ).scalar_one()
    assert anchor == "做题主动规避同类错误"


def test_reference_seed_is_idempotent_and_does_not_overwrite(client, db_session):
    from app.core.database import seed_reference_data

    db_session.execute(
        text("UPDATE stage_dict SET name = '校区自定义名称' WHERE stage_id = '三阶'")
    )
    db_session.commit()

    seed_reference_data(client.app.state.engine)

    assert _scalar(db_session, "SELECT COUNT(*) FROM stage_dict") == 10
    assert _scalar(
        db_session, "SELECT name FROM stage_dict WHERE stage_id = '三阶'"
    ) == "校区自定义名称"


def test_v1_database_upgrades_without_rewriting_legacy_stage(tmp_path: Path):
    path = tmp_path / "legacy.db"
    legacy = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with legacy.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE student ("
            "student_id TEXT PRIMARY KEY, name TEXT NOT NULL, grade TEXT, "
            "current_stage TEXT, status TEXT NOT NULL DEFAULT 'active', "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO student VALUES "
            "('S-LEGACY','旧学生','三年级','自定义阶段','active','old','old')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE teacher (teacher_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "role TEXT, status TEXT NOT NULL DEFAULT 'active', "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO teacher VALUES "
            "('T-LEGACY','旧教师','晚辅教师','active','old','old')"
        )
        connection.exec_driver_sql("PRAGMA user_version = 1")
    legacy.dispose()

    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    initialize_database(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 9
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(student)")
        }
        assert "late_care_level" in columns
        assert connection.exec_driver_sql(
            "SELECT current_stage FROM student WHERE student_id='S-LEGACY'"
        ).scalar_one() == "自定义阶段"
        teacher_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(teacher)")
        }
        assert "password_hash" in teacher_columns
        assert connection.exec_driver_sql(
            "SELECT name FROM teacher WHERE teacher_id='T-LEGACY'"
        ).scalar_one() == "旧教师"
    engine.dispose()


def test_future_database_version_is_rejected_before_any_schema_change(tmp_path: Path):
    path = tmp_path / "future.db"
    future = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with future.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sentinel (value TEXT)")
        connection.exec_driver_sql("PRAGMA user_version = 10")
    future.dispose()

    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with pytest.raises(RuntimeError, match="database has 10"):
        initialize_database(engine)
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"sentinel"}
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 10
    engine.dispose()
