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
    parent_message: str
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

        parent_message = raw.get("parent_message")
        if not isinstance(parent_message, str):
            parent_message = "" if parent_message is None else str(parent_message)

        summary = raw.get("summary")
        if not isinstance(summary, str):
            summary = "" if summary is None else str(summary)

        return cls(
            parent_message=parent_message,
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


DIRECTION_LABELS = {
    "knowledge": "知识掌握",
    "habit": "习惯养成",
    "mindset": "学习态度",
    "special_skill": "专项技能",
    "special_habit": "专项习惯",
}


class TemplateReportGenerator:
    """A rule-based generator that only uses facts present in the context.

    The template produces both a complete parent-facing ``parent_message`` and
    the internal structured analysis. The message follows the ordering in the
    product spec and omits any section whose facts are missing.
    """

    def generate(self, context: ReportContext) -> ReportOutput:
        return ReportOutput(
            parent_message=self._build_parent_message(context),
            summary=self._build_summary(context),
            strengths=[item.text for item in context.strengths][:5],
            concerns=[item.text for item in context.concerns][:5],
            suggestions=self._build_suggestions(context),
        )

    def _build_parent_message(self, context: ReportContext) -> str:
        parts: list[str] = []

        if context.feedback_count == 1:
            parts.append(
                f"{context.student_name}家长您好，本阶段的一次反馈中观察到孩子近期的学习情况如下。"
            )
        else:
            parts.append(
                f"{context.student_name}家长您好，本阶段共有 "
                f"{context.feedback_count} 次有效反馈，总体来看孩子近期的学习情况如下。"
            )

        strength_texts = [item.text for item in context.strengths]
        if len(strength_texts) == 1:
            parts.append(f"值得肯定的是，孩子在「{strength_texts[0]}」方面表现突出。")
        elif strength_texts:
            parts.append(
                f"值得肯定的是，孩子在「{strength_texts[0]}」和「{strength_texts[1]}」方面表现突出。"
            )

        concern_texts = [item.text for item in context.concerns]
        if concern_texts:
            parts.append(
                f"需要重点关注的是，孩子在「{concern_texts[0]}」方面仍有提升空间。"
            )

        parts.append(self._build_family_suggestion(context, strength_texts, concern_texts))

        parts.append(
            "后续我们会在课堂中持续关注并给予针对性指导，也请您在家多鼓励孩子，"
            "共同帮助孩子稳步进步。"
        )

        return "\n\n".join(parts)

    def _build_family_suggestion(
        self,
        context: ReportContext,
        strength_texts: list[str],
        concern_texts: list[str],
    ) -> str:
        if concern_texts:
            return (
                f"建议在家每天用少量时间，围绕「{concern_texts[0]}」做一点针对性练习，"
                "强度以孩子不感到负担为宜。"
            )
        if strength_texts:
            return (
                f"建议在家多肯定孩子在「{strength_texts[0]}」方面的努力，"
                "帮助孩子保持这份积极性。"
            )
        return "建议在家保持规律的学习节奏，并留意孩子的课堂反馈变化。"

    def _build_summary(self, context: ReportContext) -> str:
        base = (
            f"{context.student_name}在{context.class_name}的本周期内共有 "
            f"{context.feedback_count} 次有效反馈"
        )
        if not context.rating_changes:
            return base + "。"

        trends = []
        for key, change in context.rating_changes.items():
            label = DIRECTION_LABELS.get(key, key)
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
