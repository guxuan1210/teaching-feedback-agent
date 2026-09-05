"""Feedback input shapes: parsed and validated before persistence.

Ratings are plain integers here; range validation happens in the service so
the app can surface a user-facing Chinese message instead of a raw coercion
error.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DailyFeedbackInput:
    rating_knowledge: int
    rating_habit: int
    rating_mindset: int
    progress_indicators: list[str] = field(default_factory=list)
    weak_indicators: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class SpecialFeedbackInput:
    rating_skill: int
    rating_habit: int
    progress_indicators: list[str] = field(default_factory=list)
    weak_indicators: list[str] = field(default_factory=list)
    note: str | None = None
