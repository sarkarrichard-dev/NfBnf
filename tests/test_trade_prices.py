from __future__ import annotations

from index_ai.learning import (
    backfill_option_prices_for_close,
    format_trade_for_ui,
    infer_exit_ltp_from_pnl,
    record_trade,
    record_trade_outcome,
    repair_closed_trade_prices,
)


def test_infer_exit_ltp_buy_call() -> None:
    option = {"ltp": 100.0, "transaction_type": "BUY", "quantity": 65}
    assert infer_exit_ltp_from_pnl(option, 650.0, qty=65) == 110.0


def test_record_outcome_backfills_exit() -> None:
    tid = record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.72,
        option={"ltp": 50.0, "strike": 24000, "transaction_type": "BUY", "quantity": 65},
        signal={"action": "BUY_CALL", "price": 24000},
        status="PAPER_RECORDED",
    )
    record_trade_outcome(tid, 650.0)
    from index_ai.learning import connect, _row_to_trade

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
    ui = format_trade_for_ui(_row_to_trade(row))
    assert ui["entry_option_ltp"] == 50.0
    assert ui["exit_option_ltp"] == 60.0
    assert ui["exit_inferred_from_pnl"] is True


def test_repair_closed_trade_prices() -> None:
    assert repair_closed_trade_prices() >= 0
