from __future__ import annotations

from datetime import datetime

from index_ai.market_clock import (
    IST,
    MARKET_CLOSE,
    MARKET_OPEN,
    TRADING_ENTRIES_END,
    TRADING_ENTRIES_START,
    format_ist_display,
    format_ist_time_of_day,
    is_market_open,
    is_pre_open_analysis_window,
    is_square_off_window,
    is_trading_entries_allowed,
    market_status,
    parse_ist_datetime,
    session_times,
)
from index_ai.exit import estimate_pnl_rupees


def test_market_open_weekday_session() -> None:
    dt = datetime(2026, 5, 25, 10, 30, tzinfo=IST)
    assert is_market_open(dt) is True
    assert is_square_off_window(dt) is False


def test_market_closed_weekend() -> None:
    dt = datetime(2026, 5, 24, 11, 0, tzinfo=IST)
    assert is_market_open(dt) is False
    status = market_status(dt)
    assert status["phase"] == "weekend"


def test_square_off_window() -> None:
    dt = datetime(2026, 5, 25, 15, 27, tzinfo=IST)
    assert is_square_off_window(dt) is True


def test_format_ist_display_12h() -> None:
    assert format_ist_display("2026-05-29T15:25:26+05:30") == "29 May 2026, 3:25:26 PM IST"
    assert format_ist_display("2026-05-29T10:37:04+05:30") == "29 May 2026, 10:37:04 AM IST"


def test_format_ist_display_dhan_token_validity() -> None:
    assert format_ist_display("01/06/2026 18:54") == "01 Jun 2026, 6:54:00 PM IST"
    assert format_ist_display("01/06/2026 18:54 IST") == "01 Jun 2026, 6:54:00 PM IST"
    parsed = parse_ist_datetime("01/06/2026 18:54")
    assert parsed is not None
    assert parsed.hour == 18


def test_format_ist_time_of_day_12h() -> None:
    assert format_ist_time_of_day(MARKET_OPEN) == "9:15 AM"
    assert format_ist_time_of_day(MARKET_CLOSE) == "3:30 PM"


def test_pnl_estimate_buy() -> None:
    pnl = estimate_pnl_rupees(entry_ltp=100, exit_ltp=110, quantity=25, transaction_type="BUY")
    assert pnl == 250.0


def test_pre_open_analysis_window() -> None:
    dt = datetime(2026, 6, 2, 9, 25, tzinfo=IST)
    assert is_pre_open_analysis_window(dt) is True
    assert is_trading_entries_allowed(dt) is False


def test_first_entry_after_930() -> None:
    dt = datetime(2026, 6, 2, 9, 29, 59, tzinfo=IST)
    assert is_trading_entries_allowed(dt) is False
    dt = datetime(2026, 6, 2, 9, 30, tzinfo=IST)
    assert is_trading_entries_allowed(dt) is True


def test_entries_closed_at_315() -> None:
    dt = datetime(2026, 6, 2, 15, 14, tzinfo=IST)
    assert is_trading_entries_allowed(dt) is True
    dt = datetime(2026, 6, 2, 15, 15, tzinfo=IST)
    assert is_trading_entries_allowed(dt) is False


def test_square_off_from_315() -> None:
    dt = datetime(2026, 6, 2, 15, 14, tzinfo=IST)
    assert is_square_off_window(dt) is False
    dt = datetime(2026, 6, 2, 15, 15, tzinfo=IST)
    assert is_square_off_window(dt) is True


def test_market_status_pre_open_phase() -> None:
    dt = datetime(2026, 6, 2, 9, 25, tzinfo=IST)
    status = market_status(dt)
    assert status["phase"] == "pre_open_analysis"
    assert status["entries_allowed"] is False


def test_session_times_defaults() -> None:
    times = session_times()
    assert format_ist_time_of_day(times["entries_start"]) == format_ist_time_of_day(TRADING_ENTRIES_START)
    assert format_ist_time_of_day(times["entries_end"]) == format_ist_time_of_day(TRADING_ENTRIES_END)
