"""Weekly report center: HTTP routes, scoping and permission tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


class FakeRewriter:
    """A rewriter that always returns a fixed, valid candidate."""

    def rewrite(self, *, action, instruction, current_message, context):
        return "润色后的家长沟通正文"


def _rewriter_client(database_url: str) -> TestClient:
    application = create_app(database_url=database_url, rewriter=FakeRewriter())
    test_client = TestClient(application)
    test_client.post("/login", data={"name": "管理员", "password": "admin123"})
    return test_client


def _generate(client, class_id="C1", student_ids=None, start="2026-09-01", end="2026-09-07"):
    return client.post(
        "/reports/generate",
        data={
            "class_id": class_id,
            "student_ids": student_ids or ["S1"],
            "period_start": start,
            "period_end": end,
        },
        follow_redirects=False,
    )


def test_index_renders(client, report_scope):
    response = client.get("/reports")
    assert response.status_code == 200
    assert "周报中心" in response.text


def test_generate_report_from_center(client, report_scope):
    response = _generate(client)
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert "给家长的话" in detail.text
    assert "查看来源反馈" in detail.text
    assert '<details class="source-details">' in detail.text
    assert '<details class="source-details" open>' not in detail.text


def test_ai_generation_without_config_falls_back_silently(client, report_scope):
    response = _generate(client)
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    # the technical fallback note is internal and never shown in the UI
    assert "AI 未配置" not in detail.text
    assert "给家长的话" in detail.text


def test_finalize_report_blocks_further_edits(client, report_draft):
    finalized = client.post(
        f"/reports/{report_draft.report_id}/finalize", follow_redirects=False
    )
    assert finalized.status_code == 303
    edited = client.post(
        f"/reports/{report_draft.report_id}",
        data={"parent_message": "再次修改"},
    )
    assert edited.status_code == 409


def test_teacher_cannot_view_unowned_report(teacher_client, report_draft):
    response = teacher_client.get(f"/reports/{report_draft.report_id}")
    assert response.status_code == 403


def test_teacher_cannot_generate_for_unowned_class(teacher_client, report_scope):
    response = _generate(teacher_client)
    assert response.status_code == 403


def test_teacher_cannot_finalize_unowned_report(teacher_client, report_draft):
    response = teacher_client.post(f"/reports/{report_draft.report_id}/finalize")
    assert response.status_code == 403


def test_teacher_index_hides_unowned_reports(teacher_client, report_draft):
    response = teacher_client.get("/reports")
    assert response.status_code == 200
    assert report_draft.report_id not in response.text


def test_generate_invalid_dates_return_422(client, report_scope):
    response = client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "not-a-date",
            "period_end": "2026-09-07",
        },
    )
    assert response.status_code == 422
    assert "not-a-date" in response.text


def test_generate_reversed_dates_return_422(client, report_scope):
    response = client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "2026-09-07",
            "period_end": "2026-09-01",
        },
    )
    assert response.status_code == 422
    assert "2026-09-07" in response.text
    assert "2026-09-01" in response.text


def test_rewrite_preview_and_confirm(database_url, report_draft):
    client = _rewriter_client(database_url)
    report_id = report_draft.report_id

    preview = client.post(
        f"/reports/{report_id}/rewrite/preview", data={"action": "polish"}
    )
    assert preview.status_code == 200
    assert "原稿" in preview.text
    assert "修改稿" in preview.text
    assert "润色后的家长沟通正文" in preview.text

    confirmed = client.post(
        f"/reports/{report_id}/rewrite/confirm",
        data={
            "candidate": "润色后的家长沟通正文",
            "expected_original": "给家长的话",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    detail = client.get(f"/reports/{report_id}")
    assert "润色后的家长沟通正文" in detail.text


def test_rewrite_confirm_rejects_stale_original(database_url, report_draft):
    client = _rewriter_client(database_url)
    confirmed = client.post(
        f"/reports/{report_draft.report_id}/rewrite/confirm",
        data={
            "candidate": "润色后的家长沟通正文",
            "expected_original": "不是原文",
        },
    )
    assert confirmed.status_code == 409
    assert "已被更新" in confirmed.text


def test_rewrite_preview_without_rewriter_returns_503(client, report_draft):
    preview = client.post(
        f"/reports/{report_draft.report_id}/rewrite/preview", data={"action": "polish"}
    )
    assert preview.status_code == 503
    assert "改写服务不可用" in preview.text


def test_rewrite_rejected_for_finalized_report(client, report_draft):
    client.post(f"/reports/{report_draft.report_id}/finalize")
    preview = client.post(
        f"/reports/{report_draft.report_id}/rewrite/preview", data={"action": "polish"}
    )
    assert preview.status_code == 409
