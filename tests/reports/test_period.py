"""Calendar helper tests for the report center's default period."""

from __future__ import annotations

from datetime import date

from app.reports.period import last_full_week


def test_last_full_week_mid_week():
    monday, sunday = last_full_week(date(2026, 9, 10))  # Thursday
    assert monday == date(2026, 8, 31)
    assert sunday == date(2026, 9, 6)


def test_last_full_week_on_monday():
    monday, sunday = last_full_week(date(2026, 9, 7))
    assert monday == date(2026, 8, 31)
    assert sunday == date(2026, 9, 6)


def test_last_full_week_on_sunday():
    monday, sunday = last_full_week(date(2026, 9, 6))
    assert monday == date(2026, 8, 24)
    assert sunday == date(2026, 8, 30)


def test_last_full_week_crosses_month():
    monday, sunday = last_full_week(date(2026, 3, 2))  # Monday
    assert monday == date(2026, 2, 23)
    assert sunday == date(2026, 3, 1)


def test_last_full_week_crosses_year():
    monday, sunday = last_full_week(date(2026, 1, 5))  # Monday
    assert monday == date(2025, 12, 29)
    assert sunday == date(2026, 1, 4)
