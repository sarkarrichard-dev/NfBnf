"""Tests for scheduled Dhan token renew."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from index_ai import token_scheduler as ts

IST = ZoneInfo("Asia/Kolkata")


def test_daily_renew_time_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAILY_RENEW_IST", raising=False)
    assert ts.daily_renew_time().strftime("%H:%M") == "08:00"


def test_daily_renew_time_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_RENEW_IST", "07:30")
    assert ts.daily_renew_time().strftime("%H:%M") == "07:30"


def test_startup_renew_once_per_day(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("RENEW_DHAN_TOKEN_ON_STARTUP", "true")
    assert ts.startup_renew_due() is True
    ts.mark_startup_renew_done()
    assert ts.startup_renew_due() is False


def test_daily_renew_weekday_window(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("DAILY_RENEW_IST", "08:00")

    class FakeNow:
        @staticmethod
        def now_ist():
            return datetime(2026, 6, 1, 8, 5, tzinfo=IST)  # Monday

    monkeypatch.setattr(ts, "now_ist", FakeNow.now_ist)
    assert ts.daily_renew_due() is True
    ts.mark_daily_renew_done()
    assert ts.daily_renew_due() is False


def test_daily_renew_skips_weekend(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_STATE_PATH", tmp_path / "state.json")

    class FakeNow:
        @staticmethod
        def now_ist():
            return datetime(2026, 6, 6, 8, 5, tzinfo=IST)  # Saturday

    monkeypatch.setattr(ts, "now_ist", FakeNow.now_ist)
    assert ts.daily_renew_due() is False
