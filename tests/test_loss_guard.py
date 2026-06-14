from __future__ import annotations

from index_ai.learning import connect, loss_guard_for_setup, record_trade


def _closed_trade(instrument: str, action: str, pnl: float) -> None:
    trade_id = record_trade(
        mode="PAPER",
        instrument=instrument,
        action=action,
        confidence=0.7,
        option={
            "instrument": instrument,
            "transaction_type": "BUY",
            "option_type": "CALL",
            "quantity": 65,
            "strike": 24000,
        },
        signal={"action": action, "price": 24000, "confidence": 0.7},
        status="CLOSED",
    )
    with connect() as db:
        db.execute("UPDATE trades SET pnl = ?, status = 'CLOSED' WHERE id = ?", (pnl, trade_id))


def test_loss_guard_blocks_repeating_bad_index_setup(monkeypatch) -> None:
    monkeypatch.setenv("LOSS_GUARD_ENABLED", "true")
    monkeypatch.setenv("LOSS_GUARD_MIN_TRADES", "3")
    monkeypatch.setenv("LOSS_GUARD_MAX_LOSS_RATE", "0.67")
    monkeypatch.setenv("LOSS_GUARD_CONSECUTIVE_LOSSES", "2")
    _closed_trade("NIFTY", "BUY_CALL", -1000)
    _closed_trade("NIFTY", "BUY_CALL", -750)
    _closed_trade("NIFTY", "BUY_CALL", 200)

    guard = loss_guard_for_setup("NIFTY", "BUY_CALL")

    assert guard["blocked"] is True
    assert "NIFTY BUY_CALL" in guard["reason"]


def test_loss_guard_passes_when_setup_has_no_bad_history(monkeypatch) -> None:
    monkeypatch.setenv("LOSS_GUARD_ENABLED", "true")
    _closed_trade("BANKNIFTY", "BUY_PUT", 500)

    guard = loss_guard_for_setup("NIFTY", "BUY_CALL")

    assert guard["blocked"] is False
