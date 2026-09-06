from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.catalog.models import Student
from app.feedback.models import DailyFeedback


def test_foreign_keys_are_enabled(db_session):
    enabled = db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    assert enabled == 1


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
    for table in (
        "stage_dict",
        "late_care_level_dict",
        "assessment_module",
        "assessment_dimension",
        "assessment_score_anchor",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
