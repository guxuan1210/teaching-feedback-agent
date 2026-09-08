"""Context construction tests for weekly report generation."""

from __future__ import annotations

import pytest

from app.reports.context import build_report_context


def test_report_context_is_stable_and_traceable(db_session, profile_feedback):
    context = build_report_context(
        db_session, student_id="S1", class_id="C1",
        period_start="2026-09-01", period_end="2026-09-07",
    )
    assert context.student_name == "李明"
    assert context.feedback_count == 2
    assert context.source_keys == [("daily", "F1"), ("daily", "F2")]
    assert context.rating_changes["knowledge"] == 1


def test_context_excludes_void_feedback(db_session, profile_feedback):
    context = build_report_context(
        db_session, student_id="S1", class_id="C1",
        period_start="2026-09-01", period_end="2026-09-07",
    )
    assert ("daily", "F-VOID") not in context.source_keys


def test_context_raises_when_no_valid_feedback(db_session, profile_feedback):
    with pytest.raises(ValueError, match="周期内没有有效反馈"):
        build_report_context(
            db_session, student_id="S1", class_id="C1",
            period_start="2026-09-08", period_end="2026-09-14",
        )
