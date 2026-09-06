from datetime import date, time

from fastapi.testclient import TestClient

from app.catalog.repository import create_class, create_teacher
from app.main import create_app
from app.sessions.repository import create_session


def _fresh_client(database_url: str) -> TestClient:
    return TestClient(create_app(database_url=database_url))


def test_unauthenticated_redirects_to_login(database_url):
    with _fresh_client(database_url) as c:
        response = c.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_login_page_renders(database_url):
    with _fresh_client(database_url) as c:
        assert c.get("/login").status_code == 200


def test_login_success(database_url):
    with _fresh_client(database_url) as c:
        response = c.post(
            "/login",
            data={"name": "管理员", "password": "admin123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert c.get("/").status_code == 200


def test_login_wrong_password(database_url):
    with _fresh_client(database_url) as c:
        response = c.post(
            "/login",
            data={"name": "管理员", "password": "wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 422
        assert c.get("/", follow_redirects=False).status_code == 303


def test_logout_clears_session(database_url):
    with _fresh_client(database_url) as c:
        c.post("/login", data={"name": "管理员", "password": "admin123"})
        c.get("/logout")
        assert c.get("/", follow_redirects=False).status_code == 303


def test_non_admin_cannot_access_catalog(database_url, db_session):
    create_teacher(db_session, name="李老师", role="晚辅教师", password="secret")
    with _fresh_client(database_url) as c:
        c.post("/login", data={"name": "李老师", "password": "secret"})
        assert c.get("/catalog", follow_redirects=False).status_code == 403
        assert c.get("/catalog/students").status_code == 403


def test_teacher_sees_only_own_sessions(database_url, db_session):
    teacher_a = create_teacher(db_session, name="甲老师", role="晚辅教师", password="aaa")
    teacher_b = create_teacher(db_session, name="乙老师", role="晚辅教师", password="bbb")
    class_a = create_class(db_session, name="甲班", class_type="daily", head_teacher_id=teacher_a.teacher_id)
    class_b = create_class(db_session, name="乙班", class_type="daily", head_teacher_id=teacher_b.teacher_id)
    create_session(
        db_session,
        class_id=class_a.class_id,
        teacher_id=teacher_a.teacher_id,
        session_type="daily",
        session_date=date(2026, 9, 5),
        start_time=time(16, 30),
    )
    create_session(
        db_session,
        class_id=class_b.class_id,
        teacher_id=teacher_b.teacher_id,
        session_type="daily",
        session_date=date(2026, 9, 5),
        start_time=time(16, 30),
    )

    with _fresh_client(database_url) as c:
        c.post("/login", data={"name": "甲老师", "password": "aaa"})
        page = c.get("/?date=2026-09-05")
        assert "甲班" in page.text
        assert "乙班" not in page.text
