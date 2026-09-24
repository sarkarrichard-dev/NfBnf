from __future__ import annotations

from index_ai.analytics import build_pnl_index_groups
from index_ai.learning import backfill_spread_leg_exit_ltps, expand_ui_trade_to_leg_rows


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


def test_closed_spread_shows_realised_pnl_not_stale_mtm() -> None:
    # A closed spread records an aggregate exit price + realised pnl, never
    # per-leg fills. The leg view must show the realised spread pnl on leg 0,
    # not fabricate per-leg P&L from the last MTM mark (current_ltp).
    ui = {
        "id": "t-closed",
        "created_at_ist": "01 Sep 2026, 9:48 AM IST",
        "closed_at_ist": "01 Sep 2026, 3:13 PM IST",
        "instrument": "BANKNIFTY",
        "is_open": False,
        "is_paper": True,
        "status": "CLOSED",
        "quantity": 30,
        "pnl": 3510.0,
        "mtm_pnl": 3744.0,  # stale pre-close mark — must not surface
        "legs_detail": [
            {
                "transaction_type": "BUY",
                "option_type": "CE",
                "strike_display": "60100",
                "entry_ltp": 86.45,
                "current_ltp": 72.7,  # last MTM leg mark, not an exit fill
                "quantity": 30,
            },
            {
                "transaction_type": "SELL",
                "option_type": "CE",
                "strike_display": "57900",
                "entry_ltp": 722.75,
                "current_ltp": 584.2,
                "quantity": 30,
            },
        ],
    }
    rows = expand_ui_trade_to_leg_rows(ui)
    assert [r["display_pnl"] for r in rows] == [3510.0, None]
    assert rows[0]["spread_pnl"] == 3510.0
    assert rows[0]["leg_pnl"] is None and rows[1]["leg_pnl"] is None


def test_backfill_spread_leg_exits_reconciles_to_recorded_pnl() -> None:
    # Only the aggregate close (net debit 519.3) and stale per-leg marks were
    # saved. Backfill must give each leg an exit_ltp whose per-leg P&L sums to
    # the realised spread total, with the gap pushed onto the short leg.
    option = {
        "quantity": 30,
        "entry_ltp": 636.3,
        "exit_ltp": 519.3,
        "leg_ltps": [72.7, 584.2],  # implies debit 511.5 — 7.8 short of realised
        "legs": [
            {"transaction_type": "BUY", "strike": 60100, "entry_ltp": 86.45, "quantity": 30},
            {"transaction_type": "SELL", "strike": 57900, "entry_ltp": 722.75, "quantity": 30},
        ],
    }
    assert backfill_spread_leg_exit_ltps(option) is True
    buy, sell = option["legs"]
    assert buy["exit_ltp"] == 72.7 and sell["exit_ltp"] == 592.0
    buy_pnl = (buy["exit_ltp"] - buy["entry_ltp"]) * 30
    sell_pnl = (sell["entry_ltp"] - sell["exit_ltp"]) * 30
    assert round(buy_pnl + sell_pnl, 2) == 3510.0
    assert backfill_spread_leg_exit_ltps(option) is False  # idempotent


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


def test_spread_margin_shows_on_the_sell_leg_not_the_hedge() -> None:
    ui = {
        "id": "t-m", "created_at_ist": "24 Sep 2026, 12:05 PM IST", "instrument": "NIFTY",
        "is_open": False, "is_paper": True, "pnl": 890.5, "status": "CLOSED", "quantity": 65,
        "capital_deployed": 19399.0, "capital_kind": "margin",
        "legs_detail": [
            {"transaction_type": "BUY", "option_type": "CE", "strike_display": "23700", "entry_ltp": 8.15},
            {"transaction_type": "SELL", "option_type": "CE", "strike_display": "23350", "entry_ltp": 59.7},
        ],
    }
    buy, sell = expand_ui_trade_to_leg_rows(ui)
    assert sell["capital_deployed"] == 19399.0 and sell["capital_kind"] == "margin"
    assert sell["margin_source"] == "estimate"                     # older trade: no Dhan figure
    assert buy["capital_deployed"] == round(8.15 * 65, 2) and buy["capital_kind"] == "premium"

    # new trades carry Dhan's own margin (real 2026-09-24 NIFTY spread numbers)
    ui["margin_dhan"] = {"total": 52840.84, "span": 21826.35, "exposure": 30484.74,
                         "sell_leg_alone": 173861.09}
    buy, sell = expand_ui_trade_to_leg_rows(ui)
    assert sell["capital_deployed"] == 52840.84 and sell["margin_source"] == "dhan"
    assert sell["margin_without_hedge"] == 173861.09
