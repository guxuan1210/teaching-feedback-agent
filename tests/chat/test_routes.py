"""Chat HTTP routes: pages, creation, streaming, and permission guards."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.chat.routes import _channel_label


def test_channel_label_maps_known_channels_and_defaults_empty():
    assert _channel_label("web") == "网页"
    assert _channel_label("wecom") == "企业微信"
    assert _channel_label(None) == ""
    assert _channel_label("unknown") == ""


def _create_student_conversation(client, class_id="C-OWNED", student_id="S-OWNED"):
    response = client.post(
        "/chat",
        data={
            "scope_type": "student",
            "class_id": class_id,
            "student_id": student_id,
            "date_from": "2026-09-01",
            "date_to": "2026-09-08",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_chat_page_renders(chat_client):
    response = chat_client.get("/chat")
    assert response.status_code == 200
    assert "教学助手" in response.text


def test_quick_prompts_keep_short_labels_and_store_full_questions(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    response = chat_teacher_client.get(f"/chat/{conversation_id}")

    assert response.status_code == 200
    assert ">总结近期进步</button>" in response.text
    assert (
        'data-prompt="请结合时间变化，总结这个学生近期最明显的进步，并给出下一步巩固建议。"'
        in response.text
    )
    assert (
        'data-prompt="请识别这个学生当前最需要优先解决的两个薄弱项，说明依据并给出可执行建议。"'
        in response.text
    )
    assert (
        'data-prompt="请起草一段可直接发给家长的阶段反馈，先肯定具体进步，再中性说明问题，最后提出一项容易配合的建议。"'
        in response.text
    )


def test_answer_sources_are_collapsed_by_default(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    generated = chat_teacher_client.post(
        f"/chat/{conversation_id}/messages", json={"content": "总结一下"}
    )
    assert generated.status_code == 200
    response = chat_teacher_client.get(f"/chat/{conversation_id}")

    assert response.status_code == 200
    assert '<details class="msg-sources source-details">' in response.text
    assert '<details class="msg-sources source-details" open>' not in response.text


def test_chat_javascript_prefers_full_quick_prompt():
    script = (Path(__file__).parents[2] / "app" / "static" / "chat.js").read_text(
        encoding="utf-8"
    )
    assert 'this.getAttribute("data-prompt") || this.textContent' in script


def test_chat_javascript_renders_answer_sources_as_collapsed_details():
    script = (Path(__file__).parents[2] / "app" / "static" / "chat.js").read_text(
        encoding="utf-8"
    )
    assert 'document.createElement("details")' in script
    assert 'document.createElement("summary")' in script
    assert 'sources.className = "msg-sources source-details"' in script


def _chat_script():
    return (Path(__file__).parents[2] / "app" / "static" / "chat.js").read_text(
        encoding="utf-8"
    )


def test_chat_javascript_recognizes_copyable_section_heading_variants():
    script = _chat_script()
    assert "normalizeHeading" in script
    # Markdown heading markers, bold markers, and full/half-width colons.
    assert r's.replace(/^#{1,6}\s*/, "")' in script
    assert r's.replace(/^(\*\*|__)([\s\S]*?)(\*\*|__)$/, "$2")' in script
    assert r's.replace(/[:：]\s*$/, "")' in script


def test_chat_javascript_hides_actions_without_valid_section():
    script = _chat_script()
    assert "function parseCopyableSection" in script
    assert 'heading === "可复制文案"' in script
    assert 'heading === "给老师的依据"' in script
    assert "if (start === -1 || end === -1)" in script
    assert "if (!section)" in script


def test_chat_javascript_copy_strips_citations():
    script = _chat_script()
    assert "function copyText" in script
    assert r'replace(/\[S\d+\]/g, "")' in script


def test_chat_javascript_installs_copy_and_edit_actions():
    script = _chat_script()
    assert 'textContent = "复制文案"' in script
    assert 'textContent = "编辑文案"' in script
    assert 'textContent = "完成编辑"' in script
    assert 'textContent = "取消"' in script
    assert 'textContent = "已复制"' in script
    assert "function installCopyActions" in script


def test_chat_javascript_scans_history_and_streamed_done():
    script = _chat_script()
    # History messages are scanned on init.
    assert '.msg.assistant:not(.failed)' in script
    # Streamed replies install actions once the reply finishes.
    assert "installCopyActions(element)" in script


def test_chat_javascript_has_sidebar_context_menu():
    script = _chat_script()
    assert "chat-context-menu" in script
    assert 'textContent = "重命名"' in script
    assert 'textContent = "删除"' in script
    assert '"/rename"' in script
    assert '"/delete"' in script
    assert 'data-owner' in script


def test_chat_sidebar_exposes_conversation_metadata(chat_teacher_client):
    _create_student_conversation(chat_teacher_client)
    response = chat_teacher_client.get("/chat")
    assert response.status_code == 200
    assert 'data-conversation-id="' in response.text
    assert 'data-title="' in response.text
    assert 'data-owner="1"' in response.text


def test_delete_conversation_route(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    response = chat_teacher_client.post(
        f"/chat/{conversation_id}/delete", follow_redirects=False
    )
    assert response.status_code == 303
    gone = chat_teacher_client.get(f"/chat/{conversation_id}")
    assert gone.status_code == 404


def test_admin_cannot_delete_other_conversation(chat_admin_and_teacher_clients):
    admin_client, teacher_client, _application = chat_admin_and_teacher_clients
    conversation_id = _create_student_conversation(teacher_client)
    blocked = admin_client.post(
        f"/chat/{conversation_id}/delete", follow_redirects=False
    )
    assert blocked.status_code == 403


def test_create_conversation_redirects(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    detail = chat_teacher_client.get(f"/chat/{conversation_id}")
    assert detail.status_code == 200
    assert "普通学生" in detail.text


def test_send_stream_events(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    response = chat_teacher_client.post(
        f"/chat/{conversation_id}/messages", json={"content": "总结一下"}
    )
    assert response.status_code == 200
    body = response.text
    assert "event: meta" in body
    assert "event: delta" in body
    assert "event: sources" in body
    assert "event: done" in body


def test_send_requires_model_config(monkeypatch, database_url):
    for var in (
        "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "REPORT_AI_BASE_URL", "REPORT_AI_API_KEY", "REPORT_AI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    application = create_app(database_url=database_url)
    with TestClient(application) as test_client:
        test_client.post("/login", data={"name": "管理员", "password": "admin123"})
        response = test_client.post(
            "/chat/anything/messages", json={"content": "hi"}
        )
        assert response.status_code == 503


def test_unexpected_prepare_error_returns_structured_message(chat_client, monkeypatch):
    def fail_prepare(*args, **kwargs):
        raise RuntimeError("unexpected preparation failure")

    monkeypatch.setattr("app.chat.routes.service.prepare_send", fail_prepare)
    with TestClient(chat_client.app, raise_server_exceptions=False) as test_client:
        test_client.post("/login", data={"name": "管理员", "password": "admin123"})
        response = test_client.post(
            "/chat/anything/messages", json={"content": "总结近期进步"}
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == "生成准备失败，请稍后重试"


def test_teacher_cannot_view_others(chat_admin_and_teacher_clients):
    admin_client, teacher_client, _application = chat_admin_and_teacher_clients
    response = admin_client.post(
        "/chat",
        data={
            "scope_type": "class",
            "class_id": "C-OWNED",
            "student_id": "",
            "date_from": "2026-09-01",
            "date_to": "2026-09-08",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    conversation_id = response.headers["location"].rsplit("/", 1)[-1]

    blocked = teacher_client.get(f"/chat/{conversation_id}")
    assert blocked.status_code == 403


def test_admin_readonly_other_conversation(chat_admin_and_teacher_clients):
    admin_client, teacher_client, _application = chat_admin_and_teacher_clients
    conversation_id = _create_student_conversation(teacher_client)

    view = admin_client.get(f"/chat/{conversation_id}")
    assert view.status_code == 200

    blocked = admin_client.post(
        f"/chat/{conversation_id}/messages", json={"content": "hi"}
    )
    assert blocked.status_code == 403


def test_archived_conversation_blocks_send(chat_teacher_client):
    conversation_id = _create_student_conversation(chat_teacher_client)
    archived = chat_teacher_client.post(
        f"/chat/{conversation_id}/archive", follow_redirects=False
    )
    assert archived.status_code == 303

    blocked = chat_teacher_client.post(
        f"/chat/{conversation_id}/messages", json={"content": "再发一条"}
    )
    assert blocked.status_code == 403
