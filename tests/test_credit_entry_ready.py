from __future__ import annotations

from index_ai.credit_spread import credit_spread_entry_ready


def test_credit_spread_rejects_single_strike_leg() -> None:
    ok, reason = credit_spread_entry_ready(
        {"structure": "BEAR_CALL_SPREAD", "legs": [{"strike": 23650}]},
        action="SELL_BEAR_CALL_SPREAD",
    )
    assert ok is False
    assert "2 legs" in reason


def test_credit_spread_accepts_full_legs() -> None:
    legs = [
        {"strike": 23650, "security_id": 1, "transaction_type": "SELL", "option_type": "CALL"},
        {"strike": 23750, "security_id": 2, "transaction_type": "BUY", "option_type": "CALL"},
    ]
    ok, _ = credit_spread_entry_ready(
        {"structure": "BEAR_CALL_SPREAD", "legs": legs},
        action="SELL_BEAR_CALL_SPREAD",
    )
    assert ok is True
