"""Isolated fixtures: a temporary file-backed SQLite database, a FastAPI
client, and seeded catalog/session helpers.

The database lives under ``tmp_path`` (never the production ``data/``
directory), and ``client`` shares the same temp file as ``db_session`` so a
test can drive the app and inspect the ORM against one database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.catalog.models import Class, Student, Teacher
from app.core.database import build_engine, build_session_factory, initialize_database
from app.main import create_app
from app.sessions.models import ClassSession


@pytest.fixture()
def database_url(tmp_path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'teaching_demo.db').as_posix()}"


@pytest.fixture()
def engine(database_url: str):
    eng = build_engine(database_url)
    initialize_database(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    factory = build_session_factory(engine)
    session = factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def client(database_url: str):
    application = create_app(database_url=database_url)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture()
def teacher(db_session) -> Teacher:
    row = Teacher(teacher_id="T1", name="测试教师", role="晚辅教师", status="active")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def student(db_session) -> Student:
    row = Student(
        student_id="S1", name="测试学生", grade="三年级", current_stage="三阶", status="active"
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def classroom(db_session, teacher) -> Class:
    row = Class(
        class_id="C1",
        name="三年级A班",
        grade="三年级",
        class_type="daily",
        head_teacher_id=teacher.teacher_id,
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def daily_session(db_session, classroom, teacher) -> ClassSession:
    row = ClassSession(
        session_id="SESSION1",
        class_id=classroom.class_id,
        teacher_id=teacher.teacher_id,
        session_type="daily",
        course_name=None,
        session_date="2026-09-05",
        start_time="16:30",
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def special_session(db_session, classroom, teacher) -> ClassSession:
    row = ClassSession(
        session_id="SESSION2",
        class_id=classroom.class_id,
        teacher_id=teacher.teacher_id,
        session_type="special",
        course_name="数学思维",
        session_date="2026-09-05",
        start_time="17:30",
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    return row
