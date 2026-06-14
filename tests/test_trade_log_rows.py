from __future__ import annotations

from index_ai.analytics import build_pnl_index_groups
from index_ai.learning import expand_ui_trade_to_leg_rows


def test_rejected_spread_has_no_premium_in_log_rows() -> None:
    ui = {
        "id": "t-rej",
        "created_at_ist": "01 Jun 2026, 10:43 AM IST",
        "closed_at_ist": "01 Jun 2026, 10:44 AM IST",
        "instrument": "BANKNIFTY",
        "is_open": False,
        "is_live": True,
        "pnl": 0.0,
        "status": "LIVE_REJECTED",
        "display_status": "Rejected · Dhan",
        "quantity": 30,
        "legs_detail": [
            {
                "transaction_type": "SELL",
                "option_type": "CE",
                "strike_display": "54200",
                "entry_ltp": 1154.5,
                "current_ltp": 1191.55,
            },
            {
                "transaction_type": "BUY",
                "option_type": "CE",
                "strike_display": "54400",
                "entry_ltp": 1052.2,
            },
        ],
    }
    rows = expand_ui_trade_to_leg_rows(ui)
    assert len(rows) == 2
    assert rows[0]["avg_entry"] is None
    assert rows[0]["leg_mtm"] is None
    assert rows[0]["display_pnl"] is None
    assert rows[0]["spread_pnl"] is None


def test_spread_expands_to_two_leg_rows() -> None:
    ui = {
        "id": "t1",
        "created_at_ist": "01 Jun 2026, 10:43 AM IST",
        "closed_at_ist": None,
        "instrument": "BANKNIFTY",
        "is_open": True,
        "is_live": True,
        "is_paper": False,
        "status": "LIVE_TRADED",
        "display_status": "Live · filled",
        "quantity": 30,
        "mtm_pnl": 10.5,
        "legs_detail": [
            {
                "transaction_type": "SELL",
                "option_type": "CE",
                "strike_display": "54200",
                "entry_ltp": 1154.5,
                "current_ltp": 1191.55,
                "quantity": 30,
            },
            {
                "transaction_type": "BUY",
                "option_type": "CE",
                "strike_display": "54400",
                "entry_ltp": 1052.2,
                "current_ltp": 1089.6,
                "quantity": 30,
            },
        ],
    }
    rows = expand_ui_trade_to_leg_rows(ui)
    assert len(rows) == 2
    assert rows[0]["side"] == "Sell"
    assert rows[1]["side"] == "Buy"
    assert rows[0]["strike"] == "54200"
    assert rows[1]["strike"] == "54400"
    assert rows[0]["leg_mtm"] is not None
    assert rows[1]["leg_mtm"] is not None
    assert "action" not in rows[0]
    assert "SELL_BEAR" not in str(rows)


def test_pnl_index_groups_sum_leg_rows_and_totals() -> None:
    rows = [
        {
            "instrument": "NIFTY",
            "is_open": False,
            "leg_pnl": 300.0,
            "display_pnl": 300.0,
        },
        {
            "instrument": "NIFTY",
            "is_open": False,
            "leg_pnl": -100.0,
            "display_pnl": -100.0,
        },
        {
            "instrument": "BANKNIFTY",
            "is_open": True,
            "leg_mtm": -250.0,
            "display_pnl": -250.0,
        },
    ]

    groups = build_pnl_index_groups(rows)

    nifty = next(g for g in groups if g["instrument"] == "NIFTY")
    bank = next(g for g in groups if g["instrument"] == "BANKNIFTY")
    assert nifty["realized_pnl"] == 200.0
    assert nifty["total_pnl"] == 200.0
    assert bank["open_mtm"] == -250.0
    assert bank["total_pnl"] == -250.0
