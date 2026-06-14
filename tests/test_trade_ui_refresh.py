from __future__ import annotations

from index_ai.learning import format_trade_for_ui, record_trade, record_trade_outcome
from index_ai.scanner import _recent_same_action


def test_format_trade_display_status_closed() -> None:
    tid = record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.72,
        option={"strike": 24000},
        signal={"action": "BUY_CALL", "price": 24000},
        status="PAPER_RECORDED",
    )
    record_trade_outcome(tid, 500.0)
    from index_ai.learning import connect, _row_to_trade

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
    ui = format_trade_for_ui(_row_to_trade(row))
    assert ui["display_status"] == "Closed"
    assert ui["status"] == "CLOSED"


def test_recent_same_action_blocks_within_cooldown() -> None:
    instrument = "NIFTY"
    action = "BUY_CALL"
    tid = record_trade(
        mode="PAPER",
        instrument=instrument,
        action=action,
        confidence=0.7,
        option={},
        signal={"action": action},
        status="PAPER_RECORDED",
    )
    record_trade_outcome(tid, 100.0)
    assert _recent_same_action(instrument, action) is True
