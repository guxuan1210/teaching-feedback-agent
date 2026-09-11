"""Weekly report service lifecycle, AI fallback and batch isolation tests."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.reports.context import ReportContext
from app.reports.generators import ReportOutput
from app.reports.models import WeeklyReport
from app.reports.service import (
    finalize_report,
    generate_report,
    generate_reports_for_class,
    list_report_rows,
    replace_report_message,
    update_report_message,
)


class FailingGenerator:
    """A generator that always raises, to exercise the fallback path."""

    def generate(self, context: ReportContext) -> ReportOutput:
        raise RuntimeError("boom")


def test_ai_failure_falls_back_to_template(db_session, report_scope):
    result = generate_report(
        db_session,
        student_id="S1",
        class_id="C1",
        teacher_id="T1",
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="ai",
        ai_generator=FailingGenerator(),
    )
    assert result.generation_mode == "template"
    assert "AI 生成失败" in result.generation_note
    assert {(s.feedback_type, s.feedback_id) for s in result.sources} == {
        ("daily", "F1"),
        ("daily", "F2"),
    }


def test_only_one_finalized_report_per_scope(db_session, two_report_drafts):
    finalize_report(db_session, two_report_drafts[0].report_id, teacher_id="T1")
    with pytest.raises(ValueError, match="已有定稿"):
        finalize_report(db_session, two_report_drafts[1].report_id, teacher_id="T1")


def test_edit_finalized_report_is_rejected(db_session, report_scope):
    report = generate_report(
        db_session,
        student_id="S1",
        class_id="C1",
        teacher_id="T1",
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="template",
    )
    finalize_report(db_session, report.report_id, teacher_id="T1")
    with pytest.raises(ValueError, match="不可编辑"):
        update_report_message(
            db_session,
            report.report_id,
            parent_message="新正文",
        )


def test_generation_is_atomic_when_no_feedback(db_session, report_scope):
    with pytest.raises(ValueError, match="周期内没有有效反馈"):
        generate_report(
            db_session,
            student_id="S1",
            class_id="C1",
            teacher_id="T1",
            period_start="2026-09-08",
            period_end="2026-09-14",
            requested_mode="template",
        )
    count = db_session.scalar(select(func.count()).select_from(WeeklyReport))
    assert count == 0


def test_batch_generation_isolates_failures(db_session, report_scope):
    result = generate_reports_for_class(
        db_session,
        class_id="C1",
        teacher_id="T1",
        student_ids=["S1", "S-MISSING"],
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="template",
    )
    assert len(result.created) == 1
    assert len(result.errors) == 1
    assert result.errors[0]["student_id"] == "S-MISSING"
    count = db_session.scalar(select(func.count()).select_from(WeeklyReport))
    assert count == 1


def test_update_report_message_saves_parent_message(db_session, report_scope):
    report = generate_report(
        db_session,
        student_id="S1",
        class_id="C1",
        teacher_id="T1",
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="template",
    )
    updated = update_report_message(
        db_session, report.report_id, parent_message="新的家长沟通正文"
    )
    assert updated.parent_message == "新的家长沟通正文"
    # internal analysis is untouched by a message edit
    assert updated.summary == report.summary


def test_replace_report_message_rejects_stale_original(db_session, report_scope):
    report = generate_report(
        db_session,
        student_id="S1",
        class_id="C1",
        teacher_id="T1",
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="template",
    )
    update_report_message(db_session, report.report_id, parent_message="已更新的正文")
    with pytest.raises(ValueError, match="已被更新"):
        replace_report_message(
            db_session,
            report.report_id,
            parent_message="候选正文",
            expected_original="过期的原文",
        )


def test_list_report_rows_projects_student_status(db_session, report_scope):
    rows = list_report_rows(
        db_session, class_id="C1", period_start="2026-09-01", period_end="2026-09-07"
    )
    assert [row.student_id for row in rows] == ["S1"]
    assert rows[0].feedback_count == 2
    assert rows[0].status == "none"

    generate_report(
        db_session,
        student_id="S1",
        class_id="C1",
        teacher_id="T1",
        period_start="2026-09-01",
        period_end="2026-09-07",
        requested_mode="template",
    )
    rows = list_report_rows(
        db_session, class_id="C1", period_start="2026-09-01", period_end="2026-09-07"
    )
    assert rows[0].status == "draft"
    assert rows[0].excerpt
