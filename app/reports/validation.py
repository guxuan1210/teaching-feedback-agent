"""Validation for generated weekly report output.

Any generator output — template or AI — is checked against the context before
it is accepted. The checks reject empty summaries, wrong field types,
over-long content, personality labels, and explicit numbers that have no basis
in the context.
"""

from __future__ import annotations

import re

from app.reports.context import ReportContext
from app.reports.generators import ReportOutput

_PERSONALITY_LABELS = {"聪明", "天才", "笨", "懒", "内向", "外向", "调皮", "愚钝"}

_INT_RE = re.compile(r"\d+")

_ALLOWED_RATING_INTS = (1, 2, 3, 4, 5)

_PARENT_MESSAGE_MAX = 2000


def validate_report_output(
    output: ReportOutput, context: ReportContext
) -> ReportOutput:
    """Validate ``output`` against ``context`` and return it unchanged."""
    _validate_summary(output.summary)
    _validate_string_list(output.strengths, "strengths")
    _validate_string_list(output.concerns, "concerns")
    _validate_string_list(output.suggestions, "suggestions")
    _validate_personality_labels(output.summary)
    _validate_grounded_numbers(output.summary, context)
    validate_parent_message(output.parent_message, context)
    return output


def validate_parent_message(message: object, context: ReportContext) -> str:
    """Validate a parent-facing message and return it as a stripped string.

    Shared by first generation, quick/dialogue rewriting and candidate
    confirmation so every machine-written message passes the same checks.
    """
    if not isinstance(message, str) or not message.strip():
        raise ValueError("家长沟通正文不能为空")
    message = message.strip()
    if len(message) > _PARENT_MESSAGE_MAX:
        raise ValueError(f"家长沟通正文超出 {_PARENT_MESSAGE_MAX} 字限制")
    _validate_personality_labels(message)
    _validate_grounded_numbers(message, context)
    return message


def _validate_summary(summary: object) -> None:
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("周报总结不能为空")
    if len(summary) > 300:
        raise ValueError("周报总结超出 300 字限制")


def _validate_string_list(items: object, name: str) -> None:
    if not isinstance(items, list):
        raise ValueError(f"周报字段 {name} 类型不正确，应为字符串列表")
    if len(items) > 5:
        raise ValueError(f"周报字段 {name} 超出 5 项限制")
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"周报字段 {name} 必须为字符串列表")
        if len(item) > 120:
            raise ValueError(f"周报字段 {name} 单项超出 120 字限制")


def _validate_personality_labels(text: str) -> None:
    for label in _PERSONALITY_LABELS:
        if label in text:
            raise ValueError("内容包含人格标签，缺乏数据依据")


def _validate_grounded_numbers(text: str, context: ReportContext) -> None:
    for token in _INT_RE.findall(text):
        number = int(token)
        if number not in _ALLOWED_RATING_INTS and number != context.feedback_count:
            raise ValueError("内容包含未在数据依据中的数字")
