"""Parent-message rewriter tests."""

from __future__ import annotations

from app.chat.providers import ModelConfig
from app.profiles.service import IndicatorFrequency
from app.reports.context import ReportContext
from app.reports.rewriter import (
    ParentMessageRewriter,
    build_rewriter,
    build_rewriter_from_env,
)


def _context() -> ReportContext:
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


def test_rewriter_returns_candidate_only():
    captured = {}

    def invoke(user_content: str) -> str:
        captured["user_content"] = user_content
        return "  改写后的正文  "

    rewriter = ParentMessageRewriter(invoke)
    result = rewriter.rewrite(
        action="polish",
        instruction=None,
        current_message="原文",
        context=_context(),
    )
    assert result == "改写后的正文"
    assert "原文" in captured["user_content"]
    assert "李明" in captured["user_content"]
    assert "主动检查" in captured["user_content"]


def test_rewriter_defaults_to_polish_without_action_or_instruction():
    def invoke(user_content: str) -> str:
        assert "润色全文" in user_content
        return "正文"

    rewriter = ParentMessageRewriter(invoke)
    rewriter.rewrite(
        action=None, instruction=None, current_message="原文", context=_context()
    )


def test_rewriter_uses_instruction_when_present():
    captured = {}

    def invoke(user_content: str) -> str:
        captured["user_content"] = user_content
        return "正文"

    rewriter = ParentMessageRewriter(invoke)
    rewriter.rewrite(
        action=None, instruction="语气更温和", current_message="原文", context=_context()
    )
    assert "语气更温和" in captured["user_content"]


def test_build_rewriter_from_env_requires_config(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("REPORT_AI_BASE_URL", raising=False)
    monkeypatch.delenv("REPORT_AI_API_KEY", raising=False)
    monkeypatch.delenv("REPORT_AI_MODEL", raising=False)
    assert build_rewriter_from_env() is None


def test_build_rewriter_from_config():
    config = ModelConfig(base_url="http://example.test", api_key="k", model="m")
    rewriter = build_rewriter(config)
    assert isinstance(rewriter, ParentMessageRewriter)
