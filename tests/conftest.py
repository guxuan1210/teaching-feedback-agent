"""Isolated fixtures: a temporary file-backed SQLite database, a FastAPI
client, and seeded catalog/session helpers.

The database lives under ``tmp_path`` (never the production ``data/``
directory), and ``client`` shares the same temp file as ``db_session`` so a
test can drive the app and inspect the ORM against one database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.catalog.models import Class, Enrollment, Student, Teacher
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


@pytest.fixture()
def special_classroom(db_session, teacher) -> Class:
    row = Class(
        class_id="C2",
        name="数学思维班",
        grade="三年级",
        class_type="special",
        head_teacher_id=teacher.teacher_id,
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def seeded_catalog(teacher, classroom) -> Class:
    """A daily class C1 and teacher T1 exist (the ids the session tests use)."""
    return classroom


@pytest.fixture()
def session_with_roster(db_session) -> ClassSession:
    """A daily session whose roster includes an enrolled student ("李明") but
    excludes a student who left before the session date ("已离班学生")."""
    teacher = Teacher(teacher_id="T-ROSTER", name="王老师", role="晚辅教师", status="active")
    db_session.add(teacher)
    db_session.commit()

    klass = Class(
        class_id="C-ROSTER", name="三年级A班", grade="三年级", class_type="daily",
        head_teacher_id="T-ROSTER", status="active",
    )
    db_session.add(klass)
    db_session.commit()

    active_student = Student(
        student_id="S-ACTIVE", name="李明", grade="三年级", current_stage="三阶", status="active"
    )
    left_student = Student(
        student_id="S-LEFT", name="已离班学生", grade="三年级", current_stage="三阶", status="active"
    )
    db_session.add_all([active_student, left_student])
    db_session.commit()

    db_session.add_all([
        Enrollment(student_id="S-ACTIVE", class_id="C-ROSTER", start_date="2026-09-01", status="active"),
        Enrollment(
            student_id="S-LEFT", class_id="C-ROSTER",
            start_date="2026-09-01", end_date="2026-09-04", status="left",
        ),
    ])
    db_session.commit()

    row = ClassSession(
        session_id="SESSION-ROSTER",
        class_id="C-ROSTER",
        teacher_id="T-ROSTER",
        session_type="daily",
        course_name=None,
        session_date="2026-09-05",
        start_time="16:30",
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    return row
