"""Weekly report center: HTTP routes, scoping and permission tests."""

from __future__ import annotations


def test_generate_template_report_from_center(client, report_scope):
    response = client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "generation_mode": "template",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert "周报草稿" in detail.text
    assert "查看来源反馈" in detail.text


def test_finalize_report_blocks_further_edits(client, report_draft):
    finalized = client.post(
        f"/reports/{report_draft.report_id}/finalize", follow_redirects=False
    )
    assert finalized.status_code == 303
    edited = client.post(
        f"/reports/{report_draft.report_id}",
        data={
            "summary": "再次修改",
            "strengths": "[]",
            "concerns": "[]",
            "suggestions": "[]",
        },
    )
    assert edited.status_code == 409


def test_teacher_cannot_view_unowned_report(teacher_client, report_draft):
    response = teacher_client.get(f"/reports/{report_draft.report_id}")
    assert response.status_code == 403


def test_teacher_cannot_generate_for_unowned_class(teacher_client, report_scope):
    response = teacher_client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "generation_mode": "template",
        },
    )
    assert response.status_code == 403


def test_teacher_cannot_finalize_unowned_report(teacher_client, report_draft):
    response = teacher_client.post(f"/reports/{report_draft.report_id}/finalize")
    assert response.status_code == 403


def test_teacher_index_hides_unowned_reports(teacher_client, report_draft):
    response = teacher_client.get("/reports")
    assert response.status_code == 200
    assert report_draft.report_id not in response.text


def test_ai_generation_without_config_shows_fallback_notice(client, report_scope):
    response = client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "generation_mode": "ai",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert "AI 未配置" in detail.text


def test_generate_invalid_dates_return_422(client, report_scope):
    response = client.post(
        "/reports/generate",
        data={
            "class_id": "C1",
            "student_ids": ["S1"],
            "period_start": "not-a-date",
            "period_end": "2026-09-07",
            "generation_mode": "template",
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
            "generation_mode": "template",
        },
    )
    assert response.status_code == 422
    assert "2026-09-07" in response.text
    assert "2026-09-01" in response.text
