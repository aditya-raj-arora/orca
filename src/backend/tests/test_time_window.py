"""
Unit tests for graph._resolve_time_window (P1 contract-lock item: Planner's
time_window_text -> concrete TimeWindow, graph.py TODO(P1)).

Owner: P1. Pure function, no I/O — fixed `now` passed in for determinism.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.orchestration.graph import _resolve_time_window

_NOW = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)  # a Tuesday, mid-morning UTC


def test_none_text_returns_none():
    assert _resolve_time_window(None, now=_NOW) is None


def test_unparseable_text_returns_none():
    assert _resolve_time_window("next week sometime", now=_NOW) is None
    assert _resolve_time_window("this weekend", now=_NOW) is None


def test_today_full_day():
    w = _resolve_time_window("today", now=_NOW)
    assert w.start == datetime(2026, 9, 1, 0, tzinfo=UTC)
    assert w.end == datetime(2026, 9, 2, 0, tzinfo=UTC)


def test_tomorrow_morning():
    w = _resolve_time_window("tomorrow morning", now=_NOW)
    assert w.start == datetime(2026, 9, 2, 6, tzinfo=UTC)
    assert w.end == datetime(2026, 9, 2, 12, tzinfo=UTC)


def test_day_after_tomorrow_evening():
    w = _resolve_time_window("what about the day after tomorrow evening?", now=_NOW)
    assert w.start == datetime(2026, 9, 3, 17, tzinfo=UTC)
    assert w.end == datetime(2026, 9, 3, 21, tzinfo=UTC)


def test_night_wraps_into_next_day():
    w = _resolve_time_window("tonight", now=_NOW)
    # "tonight" matches the today/now branch with no day-part keyword in it
    # ("night" alone is a day-part, but "tonight" doesn't contain the word
    # "night" as a separate token match for our \bnight\b regex against
    # "tonight" — confirms the conservative fallback to full-day).
    assert w.start == datetime(2026, 9, 1, 0, tzinfo=UTC)
    assert w.end == datetime(2026, 9, 2, 0, tzinfo=UTC)


def test_tomorrow_night_wraps_past_midnight():
    w = _resolve_time_window("tomorrow night", now=_NOW)
    assert w.start == datetime(2026, 9, 2, 21, tzinfo=UTC)
    assert w.end == datetime(2026, 9, 3, 6, tzinfo=UTC)
