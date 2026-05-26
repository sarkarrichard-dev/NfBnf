from __future__ import annotations

from datetime import datetime

from index_ai.market_clock import (
    IST,
    is_market_open,
    is_square_off_window,
    market_status,
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


def test_pnl_estimate_buy() -> None:
    pnl = estimate_pnl_rupees(entry_ltp=100, exit_ltp=110, quantity=25, transaction_type="BUY")
    assert pnl == 250.0
