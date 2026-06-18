from __future__ import annotations

from datetime import datetime

from index_ai.learning import record_trade, record_trade_outcome
from index_ai.market_clock import IST
from index_ai.trade_cleanup import remove_trades, run_trade_cleanup, scan_trade_cleanup

_SESSION_ISO = datetime(2026, 6, 2, 10, 30, tzinfo=IST).isoformat(timespec="seconds")


def _seed_duplicate_buy_calls(monkeypatch) -> list[str]:
    monkeypatch.setattr("index_ai.learning.now_ist_iso", lambda: _SESSION_ISO)
    ids: list[str] = []
    for _ in range(2):
        tid = record_trade(
            mode="PAPER",
            instrument="NIFTY",
            action="BUY_CALL",
            confidence=0.72,
            option={},
            signal={"action": "BUY_CALL", "price": 24000, "confidence": 0.72},
            status="PAPER_RECORDED",
        )
        record_trade_outcome(tid, 500.0)
        ids.append(tid)
    return ids


def test_scan_finds_duplicate_incomplete(monkeypatch) -> None:
    _seed_duplicate_buy_calls(monkeypatch)
    scan = scan_trade_cleanup(limit=50)
    assert scan["remove_count"] >= 1
    assert scan["by_reason"].get("duplicate", 0) >= 1 or scan["by_reason"].get("incomplete", 0) >= 1


def test_run_cleanup_removes_flagged(monkeypatch) -> None:
    _seed_duplicate_buy_calls(monkeypatch)
    before = scan_trade_cleanup(limit=50)
    result = run_trade_cleanup()
    assert result["removed"]["trades_removed"] >= 1
    after = scan_trade_cleanup(limit=50)
    assert after["remove_count"] < before["remove_count"]


def test_remove_trades_by_ids(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.learning.now_ist_iso", lambda: _SESSION_ISO)
    tid = record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_PUT",
        confidence=0.6,
        option={},
        signal={"action": "BUY_PUT", "price": 24000},
        status="PAPER_RECORDED",
    )
    removed = remove_trades([tid])
    assert removed["trades_removed"] == 1
