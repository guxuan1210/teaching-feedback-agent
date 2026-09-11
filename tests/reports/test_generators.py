"""Template generation and output validation tests."""

from __future__ import annotations

import httpx
import pytest

from app.profiles.service import IndicatorFrequency
from app.reports.context import ReportContext
from app.reports.generators import (
    AIReportGenerator,
    ReportOutput,
    TemplateReportGenerator,
)
from app.reports.providers import build_ai_generator_from_env, build_openai_invoke
from app.reports.validation import validate_parent_message, validate_report_output


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


def _output(parent_message="给家长的话", **overrides) -> ReportOutput:
    values = dict(
        parent_message=parent_message,
        summary="总结",
        strengths=[],
        concerns=[],
        suggestions=[],
    )
    values.update(overrides)
    return ReportOutput(**values)


def test_template_generator_returns_grounded_sections(report_context):
    result = TemplateReportGenerator().generate(report_context)
    validated = validate_report_output(result, report_context)
    assert validated.parent_message
    assert "李明" in validated.parent_message
    assert validated.summary
    assert validated.strengths
    assert all(isinstance(item, str) for item in validated.suggestions)


def test_template_single_feedback_avoids_trend_claim():
    context = ReportContext(
        student_name="李明",
        class_name="三年级A班",
        period_start="2026-09-01",
        period_end="2026-09-07",
        feedback_count=1,
        rating_changes={},
        strengths=[IndicatorFrequency("P-CHECK", "主动检查", "daily_h_progress", 1)],
        concerns=[],
        recent_notes=[],
        source_keys=[("daily", "F1")],
    )
    result = TemplateReportGenerator().generate(context)
    assert "一次反馈" in result.parent_message
    assert "趋势" not in result.parent_message


def test_validator_rejects_unsupported_number(report_context):
    output = _output(parent_message="成绩提高了 30 分", summary="成绩提高了 30 分")
    with pytest.raises(ValueError, match="数据依据"):
        validate_report_output(output, report_context)


def test_validator_rejects_empty_summary(report_context):
    output = _output(summary="   ")
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)


def test_validator_rejects_empty_parent_message(report_context):
    output = _output(parent_message="   ")
    with pytest.raises(ValueError, match="家长沟通正文不能为空"):
        validate_report_output(output, report_context)


@pytest.mark.parametrize(
    "output",
    [
        _output(parent_message="好" * 2001),
        _output(summary="好" * 301),
        _output(strengths=["长" * 121]),
        _output(strengths=[str(i) for i in range(6)]),
    ],
)
def test_validator_rejects_oversize_content(report_context, output):
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)


def test_validator_rejects_personality_label(report_context):
    output = _output(parent_message="这个孩子很聪明")
    with pytest.raises(ValueError):
        validate_report_output(output, report_context)


def test_validate_parent_message_strips_and_accepts(report_context):
    result = validate_parent_message("  给家长的话  ", report_context)
    assert result == "给家长的话"


def test_ai_generator_returns_report_output(report_context):
    generator = AIReportGenerator(
        lambda payload: {
            "parent_message": "给家长的话",
            "summary": "总结",
            "strengths": ["a"],
            "concerns": [],
            "suggestions": [],
        }
    )
    result = generator.generate(report_context)
    assert isinstance(result, ReportOutput)
    assert result.summary == "总结"
    assert result.parent_message == "给家长的话"


def test_report_output_from_mapping_coerces_fields():
    result = ReportOutput.from_mapping(
        {
            "parent_message": "正文",
            "summary": "s",
            "strengths": [1],
            "concerns": None,
            "suggestions": [],
        }
    )
    assert result.parent_message == "正文"
    assert result.summary == "s"
    assert result.strengths == ["1"]
    assert result.concerns == []
    assert result.suggestions == []


def test_build_ai_generator_from_env_requires_all_vars(monkeypatch):
    monkeypatch.delenv("REPORT_AI_BASE_URL", raising=False)
    monkeypatch.delenv("REPORT_AI_API_KEY", raising=False)
    monkeypatch.delenv("REPORT_AI_MODEL", raising=False)
    assert build_ai_generator_from_env() is None


def test_openai_invoke_parses_content_without_network():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"parent_message":"正文","summary":"hi","strengths":[],"concerns":[],"suggestions":[]}'
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    invoke = build_openai_invoke(
        "http://example.test", "k", "m", transport=transport
    )
    result = invoke({"anything": "payload"})
    assert result == {
        "parent_message": "正文",
        "summary": "hi",
        "strengths": [],
        "concerns": [],
        "suggestions": [],
    }
