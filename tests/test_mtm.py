from __future__ import annotations

from index_ai.mtm import compute_mtm_pnl


def test_compute_mtm_long_call() -> None:
    trade = {
        "option": {"ltp": 100.0, "quantity": 25, "transaction_type": "BUY"},
    }
    pnl = compute_mtm_pnl(trade, 110.0)
    assert pnl == 250.0
