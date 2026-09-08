"""Weekly report generation: the offline template and a generator interface.

A generator only consumes a :class:`~app.reports.context.ReportContext` and
produces a :class:`ReportOutput`. The template generator stays fully offline
and introduces no facts beyond the context; an AI generator (added in a later
task) must satisfy the same ``ReportGenerator`` protocol and pass the same
validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.reports.context import ReportContext


@dataclass(frozen=True)
class ReportOutput:
    summary: str
    strengths: list[str]
    concerns: list[str]
    suggestions: list[str]

    @classmethod
    def from_mapping(cls, raw: dict) -> "ReportOutput":
        """Coerce a raw mapping (e.g. a model response) into a ``ReportOutput``."""

        def _str_list(value: object) -> list[str]:
            if value is None:
                return []
            if not isinstance(value, list):
                value = [value]
            return [str(item) for item in value]

        summary = raw.get("summary")
        if not isinstance(summary, str):
            summary = "" if summary is None else str(summary)

        return cls(
            summary=summary,
            strengths=_str_list(raw.get("strengths")),
            concerns=_str_list(raw.get("concerns")),
            suggestions=_str_list(raw.get("suggestions")),
        )


class ReportGenerator(Protocol):
    def generate(self, context: ReportContext) -> ReportOutput: ...


class AIReportGenerator:
    """An AI-backed generator that delegates to an injected invoke callable."""

    def __init__(self, invoke: Callable[[dict], dict]) -> None:
        self._invoke = invoke

    def generate(self, context: ReportContext) -> ReportOutput:
        payload = context.to_prompt_payload()
        raw = self._invoke(payload)
        return ReportOutput.from_mapping(raw)


_DIRECTION_LABELS = {
    "knowledge": "知识掌握",
    "habit": "习惯养成",
    "mindset": "学习态度",
    "special_skill": "专项技能",
    "special_habit": "专项习惯",
}


class TemplateReportGenerator:
    """A rule-based generator that only uses facts present in the context."""

    def generate(self, context: ReportContext) -> ReportOutput:
        return ReportOutput(
            summary=self._build_summary(context),
            strengths=[item.text for item in context.strengths][:5],
            concerns=[item.text for item in context.concerns][:5],
            suggestions=self._build_suggestions(context),
        )

    def _build_summary(self, context: ReportContext) -> str:
        base = (
            f"{context.student_name}在{context.class_name}的本周期内共有 "
            f"{context.feedback_count} 次有效反馈"
        )
        if not context.rating_changes:
            return base + "。"

        trends = []
        for key, change in context.rating_changes.items():
            label = _DIRECTION_LABELS.get(key, key)
            if change > 0:
                direction = "上升"
            elif change < 0:
                direction = "下降"
            else:
                direction = "持平"
            trends.append(f"{label}评分较期初{direction}")

        return base + "；" + "；".join(trends) + "。"

    def _build_suggestions(self, context: ReportContext) -> list[str]:
        suggestions: list[str] = []
        for item in context.strengths[:3]:
            suggestions.append(f"继续保持「{item.text}」的良好表现")
        for item in context.concerns[:3]:
            suggestions.append(f"围绕「{item.text}」加强针对性练习")
        if not suggestions:
            suggestions.append("保持当前学习节奏，关注课堂反馈的变化")
        return suggestions[:5]
