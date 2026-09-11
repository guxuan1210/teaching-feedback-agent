from sqlalchemy import inspect, select


def test_database_initializes_wecom_tables(engine):
    table_names = set(inspect(engine).get_table_names())
    assert {
        "teacher_wecom_binding",
        "wecom_binding_code",
        "wecom_chat_state",
        "wecom_inbound_message",
    } <= table_names


def test_logged_in_teacher_can_generate_and_revoke_binding_code(teacher_client):
    page = teacher_client.get("/account")
    assert page.status_code == 200
    assert "绑定企业微信" in page.text

    created = teacher_client.post(
        "/account/wecom-binding-code", follow_redirects=False
    )
    assert created.status_code == 303
    page = teacher_client.get(created.headers["location"])
    assert "10 分钟内" in page.text
    assert "绑定 " in page.text

    revoked = teacher_client.post("/account/wecom-unbind", follow_redirects=False)
    assert revoked.status_code == 303
    assert revoked.headers["location"] == "/account?unbound=1"


def test_account_requires_login(database_url):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(database_url=database_url)) as anonymous:
        response = anonymous.get("/account", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
