"""Constraint and lifecycle tests for the weekly report persistence layer."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.reports.models import WeeklyReport, WeeklyReportSource


def _report(student, classroom, teacher, **overrides) -> WeeklyReport:
    values = dict(
        report_id="R1",
        student_id=student.student_id,
        class_id=classroom.class_id,
        teacher_id=teacher.teacher_id,
        period_start="2026-09-01",
        period_end="2026-09-07",
        generation_mode="template",
        status="draft",
        parent_message="给家长的话",
        summary="总结",
        strengths="[]",
        concerns="[]",
        suggestions="[]",
    )
    values.update(overrides)
    return WeeklyReport(**values)


def test_weekly_report_rejects_reversed_period(db_session, student, classroom, teacher):
    report = WeeklyReport(
        report_id="R1", student_id=student.student_id,
        class_id=classroom.class_id, teacher_id=teacher.teacher_id,
        period_start="2026-09-07", period_end="2026-09-01",
        generation_mode="template", status="draft",
        parent_message="给家长的话",
        summary="总结", strengths="[]", concerns="[]", suggestions="[]",
    )
    db_session.add(report)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_weekly_report_rejects_invalid_generation_mode(
    db_session, student, classroom, teacher
):
    report = _report(student, classroom, teacher, generation_mode="gpt")
    db_session.add(report)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_weekly_report_rejects_invalid_status(db_session, student, classroom, teacher):
    report = _report(student, classroom, teacher, status="published")
    db_session.add(report)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_duplicate_report_source_is_rejected(db_session, student, classroom, teacher):
    report = _report(student, classroom, teacher, report_id="R-DUP")
    db_session.add(report)
    db_session.commit()
    db_session.add_all([
        WeeklyReportSource(report_id="R-DUP", feedback_type="daily", feedback_id="F1"),
        WeeklyReportSource(report_id="R-DUP", feedback_type="daily", feedback_id="F1"),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_report_cascades_to_sources(db_session, student, classroom, teacher):
    report = _report(student, classroom, teacher, report_id="R-CASCADE")
    db_session.add(report)
    db_session.commit()
    db_session.add(
        WeeklyReportSource(report_id="R-CASCADE", feedback_type="daily", feedback_id="F1")
    )
    db_session.commit()

    db_session.delete(report)
    db_session.commit()

    remaining = db_session.scalar(
        select(WeeklyReportSource).where(
            WeeklyReportSource.report_id == "R-CASCADE"
        )
    )
    assert remaining is None
