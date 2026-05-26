from __future__ import annotations

from index_ai.learning import format_trade_for_ui, option_leg_fields


def test_option_leg_fields_buy_call_ce() -> None:
    trade = {
        "instrument": "NIFTY",
        "action": "BUY_CALL",
        "option": {
            "option_type": "CALL",
            "strike": 25200,
            "transaction_type": "BUY",
            "quantity": 75,
            "ltp": 120.5,
        },
        "signal": {"action": "BUY_CALL", "price": 25180},
    }
    leg = option_leg_fields(trade)
    assert leg["option_side"] == "CE"
    assert leg["leg_display"] == "Buy 25200 CE"
    assert "25200 CE" in leg["position_summary"]

    ui = format_trade_for_ui({**trade, "created_at": "2026-05-26T10:00:00+05:30", "mode": "PAPER"})
    assert ui["option_side"] == "CE"
    assert ui["strike_display"] == "25200"


def test_option_leg_fields_sell_put_from_action() -> None:
    trade = {
        "instrument": "BANKNIFTY",
        "action": "BUY_PUT",
        "option": {"strike": 55500, "transaction_type": "SELL"},
    }
    leg = option_leg_fields(trade)
    assert leg["option_side"] == "PE"
    assert leg["leg_display"] == "Sell 55500 PE"
