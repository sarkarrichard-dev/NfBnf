from __future__ import annotations

from index_ai.capital_required import compute_capital_required, format_capital_summary
from index_ai.instruments import get_instrument


def _bull_put_option() -> dict:
    return {
        "structure": "BULL_PUT_SPREAD",
        "quantity": 65,
        "legs": [
            {
                "transaction_type": "SELL",
                "option_type": "PUT",
                "strike": 24000,
                "ltp": 80.0,
            },
            {
                "transaction_type": "BUY",
                "option_type": "PUT",
                "strike": 23900,
                "ltp": 40.0,
            },
        ],
    }


def test_credit_spread_capital(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.capital_required.get_lots_per_trade", lambda: 1)
    cap = compute_capital_required(_bull_put_option(), get_instrument("NIFTY"))
    assert cap is not None
    assert cap["capital_kind"] == "margin"
    assert cap["capital_required_rupees"] == 3900.0
    assert cap["net_credit_rupees"] == 2600.0
    assert len(cap["legs"]) == 2
    assert cap["legs"][0]["flow_rupees"] == 5200.0
    assert cap["legs"][1]["flow_rupees"] == -2600.0
    assert "Margin" in cap["summary"]


def test_buy_capital(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.capital_required.get_lots_per_trade", lambda: 2)
    inst = get_instrument("BANKNIFTY")
    qty = inst.lot_size * 2
    cap = compute_capital_required(
        {
            "transaction_type": "BUY",
            "option_type": "CALL",
            "strike": 52000,
            "ltp": 150.0,
            "quantity": qty,
        },
        inst,
    )
    assert cap is not None
    assert cap["capital_kind"] == "premium"
    assert cap["capital_required_rupees"] == round(150.0 * qty, 2)
    assert len(cap["legs"]) == 1
    assert "Premium" in format_capital_summary(cap)
