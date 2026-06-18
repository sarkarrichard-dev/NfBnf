from __future__ import annotations

from datetime import datetime

from index_ai.analytics import _filter_session_entries
from index_ai.execution_safety import validate_execution_plan
from index_ai.instruments import get_instrument
from index_ai.market_clock import IST, is_entry_session_timestamp, now_ist


def test_entry_session_blocked_on_weekend_evening() -> None:
    dt = datetime(2026, 6, 14, 20, 0, tzinfo=IST)  # Sunday
    assert is_entry_session_timestamp(dt) is False


def test_entry_session_allowed_midweek() -> None:
    dt = datetime(2026, 6, 2, 10, 30, tzinfo=IST)  # Tuesday
    assert is_entry_session_timestamp(dt) is True


def test_validate_execution_plan_blocks_weekend(monkeypatch) -> None:
    sunday = datetime(2026, 6, 14, 20, 0, tzinfo=IST)
    monkeypatch.setattr("index_ai.market_clock.now_ist", lambda: sunday)
    check = validate_execution_plan(
        app_settings=__import__("index_ai.config", fromlist=["settings"]).settings(),
        instrument=get_instrument("NIFTY"),
        signal={"action": "BUY_CALL", "confidence": 0.8, "strategy_mode": "cpr_trend"},
        option={
            "instrument": "NIFTY",
            "transaction_type": "BUY",
            "option_type": "CE",
            "quantity": 65,
            "security_id": 1,
            "segment": "NSE_FNO",
            "strike": 24000,
        },
        min_confidence=0.5,
    )
    assert not check.ok
    assert check.code == "market_closed"


def test_filter_session_entries_excludes_weekend_rows() -> None:
    trades = [
        {"created_at": "2026-06-14T20:00:00+05:30", "pnl": 100},
        {"created_at": "2026-06-12T10:30:00+05:30", "pnl": 50},
    ]
    filtered = _filter_session_entries(trades)
    assert len(filtered) == 1
    assert filtered[0]["pnl"] == 50
