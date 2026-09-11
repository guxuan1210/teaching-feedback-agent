"""Chat test helpers: fake streaming providers and provider-injected clients."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.core.security import hash_password
from app.feedback.models import DailyFeedback
from app.main import create_app
from app.sessions.models import ClassSession


class FakeChatProvider:
    model = "fake-model"

    def __init__(self, chunks=("你好", "，这是", "测试 [S1]")):
        self._chunks = chunks

    def stream(self, messages):
        for chunk in self._chunks:
            yield chunk


class FailingChatProvider(FakeChatProvider):
    def stream(self, messages):
        raise RuntimeError("upstream down")
        yield  # pragma: no cover - keeps this a generator


class PartialFailChatProvider(FakeChatProvider):
    def stream(self, messages):
        yield "部分"
        yield "文本"
        raise RuntimeError("mid fail")


@pytest.fixture()
def chat_client(database_url):
    application = create_app(database_url=database_url, chat_provider=FakeChatProvider())
    with TestClient(application) as test_client:
        test_client.post("/login", data={"name": "管理员", "password": "admin123"})
        yield test_client


@pytest.fixture()
def chat_teacher_client(database_url):
    application = create_app(database_url=database_url, chat_provider=FakeChatProvider())
    _seed_owned_class(application.state.session_factory)
    with TestClient(application) as test_client:
        test_client.post("/login", data={"name": "普通老师", "password": "pass123"})
        yield test_client


@pytest.fixture()
def chat_admin_and_teacher_clients(database_url):
    application = create_app(database_url=database_url, chat_provider=FakeChatProvider())
    _seed_owned_class(application.state.session_factory)

    admin_client = TestClient(application)
    admin_client.post("/login", data={"name": "管理员", "password": "admin123"})
    teacher_client = TestClient(application)
    teacher_client.post("/login", data={"name": "普通老师", "password": "pass123"})

    yield admin_client, teacher_client, application

    admin_client.close()
    teacher_client.close()


def _seed_owned_class(session_factory):
    with session_factory() as session:
        session.add(
            Teacher(
                teacher_id="T-OTHER", name="普通老师", role="晚辅教师",
                password_hash=hash_password("pass123"), status="active",
            )
        )
        session.commit()
        session.add(
            Class(
                class_id="C-OWNED", name="普通晚辅班", grade="三年级",
                class_type="daily", head_teacher_id="T-OTHER", status="active",
            )
        )
        session.commit()
        session.add(
            Student(
                student_id="S-OWNED", name="普通学生", grade="三年级",
                current_stage="三阶", status="active",
            )
        )
        session.commit()
        session.add(
            Enrollment(
                student_id="S-OWNED", class_id="C-OWNED",
                start_date="2026-09-01", status="active",
            )
        )
        session.commit()


def seed_chat_feedback(session_factory):
    """Seed T1/C1/S1 with one active daily feedback record for source tests."""
    with session_factory() as session:
        session.add(
            Teacher(teacher_id="T1", name="王老师", role="晚辅教师", status="active")
        )
        session.commit()
        session.add(
            Class(
                class_id="C1", name="三年级A班", grade="三年级",
                class_type="daily", head_teacher_id="T1", status="active",
            )
        )
        session.commit()
        session.add(
            Student(
                student_id="S1", name="李明", grade="三年级",
                current_stage="三阶", status="active",
            )
        )
        session.commit()
        session.add(
            Enrollment(
                student_id="S1", class_id="C1", start_date="2026-09-01", status="active"
            )
        )
        session.commit()
        session.add(
            ClassSession(
                session_id="SESSION1", class_id="C1", teacher_id="T1",
                session_type="daily", course_name=None, session_date="2026-09-05",
                start_time="16:30", status="active",
            )
        )
        session.commit()
        session.add(
            DailyFeedback(
                feedback_id="F1", session_id="SESSION1", student_id="S1",
                rating_knowledge=4, rating_habit=4, rating_mindset=4,
                note="表现良好", status="active",
            )
        )
        session.commit()
