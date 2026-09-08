"""Template generation and output validation tests."""

from __future__ import annotations

import pytest

from app.profiles.service import IndicatorFrequency
from app.reports.context import ReportContext
from app.reports.generators import ReportOutput, TemplateReportGenerator
from app.reports.validation import validate_report_output


@pytest.fixture()
def report_context() -> ReportContext:
    return ReportContext(
        student_name="李明",
        class_name="三年级A班",
        period_start="2026-09-01",
        period_end="2026-09-07",
        feedback_count=2,
        rating_changes={"knowledge": 1},
        strengths=[IndicatorFrequency("P-CHECK", "主动检查", "daily_h_progress", 2)],
        concerns=[IndicatorFrequency("W-CHECK", "检查不仔细", "daily_h_weak", 1)],
        recent_notes=["今日表现良好"],
        source_keys=[("daily", "F1"), ("daily", "F2")],
    )


def test_template_generator_returns_grounded_sections(report_context):
    result = TemplateReportGenerator().generate(report_context)
    validated = validate_report_output(result, report_context)
    assert validated.summary
    assert validated.strengths
    assert all(isinstance(item, str) for item in validated.suggestions)


def test_validator_rejects_unsupported_number(report_context):
    output = ReportOutput(
        summary="成绩提高了 30 分", strengths=[], concerns=[], suggestions=[]
    )
    with pytest.raises(ValueError, match="数据依据"):
        validate_report_output(output, report_context)


def test_validator_rejects_empty_summary(report_context):
    output = ReportOutput(
        summary="   ", strengths=[], concerns=[], suggestions=[]
    )
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)


@pytest.mark.parametrize(
    "output",
    [
        ReportOutput(summary="好" * 301, strengths=[], concerns=[], suggestions=[]),
        ReportOutput(
            summary="正常总结", strengths=["长" * 121], concerns=[], suggestions=[]
        ),
        ReportOutput(
            summary="正常总结",
            strengths=[str(i) for i in range(6)],
            concerns=[],
            suggestions=[],
        ),
    ],
)
def test_validator_rejects_oversize_content(report_context, output):
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)


def test_validator_rejects_personality_label(report_context):
    output = ReportOutput(
        summary="这个孩子很聪明", strengths=[], concerns=[], suggestions=[]
    )
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)
