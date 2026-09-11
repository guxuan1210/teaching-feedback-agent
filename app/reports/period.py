"""Calendar helpers for the weekly report center.

The default period is the most recent complete natural week (Monday through
Sunday), so a report defaults to a whole week that has already finished.
"""

from __future__ import annotations

from datetime import date, timedelta


def last_full_week(today: date) -> tuple[date, date]:
    """Return ``(monday, sunday)`` for the most recent complete natural week."""
    # ``today.weekday()`` is 0 for Monday … 6 for Sunday. Back up to last
    # week's Monday, then Sunday is six days later.
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def default_period() -> tuple[str, str]:
    """Return the default report period as ISO date strings."""
    monday, sunday = last_full_week(date.today())
    return monday.isoformat(), sunday.isoformat()
