from __future__ import annotations

from index_ai.strategies.credit_spread import (
    attach_credit_risk_metrics,
    evaluate_credit_open_trade,
    init_credit_trail_meta,
    mark_to_close_debit,
    max_loss_points,
    net_credit_points,
    spread_pnl_rupees,
)
from index_ai.instruments import get_instrument
from index_ai.trailing import evaluate_open_trade


def _bull_put_legs() -> list[dict]:
    return [
        {
            "transaction_type": "SELL",
            "option_type": "PUT",
            "strike": 24000,
            "ltp": 80.0,
            "security_id": 1001,
            "segment": "NSE_FNO",
        },
        {
            "transaction_type": "BUY",
            "option_type": "PUT",
            "strike": 23900,
            "ltp": 40.0,
            "security_id": 1002,
            "segment": "NSE_FNO",
        },
    ]


def test_net_credit_and_pnl() -> None:
    legs = _bull_put_legs()
    credit = net_credit_points(legs)
    assert credit == 40.0
    debit = mark_to_close_debit(legs, [60.0, 30.0])
    assert debit == 30.0
    pnl = spread_pnl_rupees(entry_credit=credit, close_debit=debit, quantity=65)
    assert pnl == round((40 - 30) * 65, 2)


def test_max_loss_bull_put() -> None:
    legs = _bull_put_legs()
    credit = net_credit_points(legs)
    loss_pts = max_loss_points(legs, "BULL_PUT_SPREAD", credit)
    assert loss_pts == 60.0  # 100 wing - 40 credit


def test_attach_credit_risk_metrics() -> None:
    inst = get_instrument("NIFTY")
    option = attach_credit_risk_metrics(
        {
            "legs": _bull_put_legs(),
            "structure": "BULL_PUT_SPREAD",
            "quantity": 65,
        },
        inst,
    )
    assert option["max_profit_rupees"] == round(40 * 65, 2)
    assert option["max_loss_rupees"] == round(60 * 65, 2)


def test_init_credit_trail_meta_uses_the_passed_in_stop_pct() -> None:
    """A HIGH_VOL day still sells, just on a tighter stop -- executor.py builds
    a StrategyParams with a lower credit_stop_loss_pct and passes it in; this
    is the piece of plumbing that actually shrinks the stop."""
    from dataclasses import replace

    from index_ai.strategies.strategy_params import get_strategy_params

    inst = get_instrument("NIFTY")
    option = attach_credit_risk_metrics(
        {"legs": _bull_put_legs(), "structure": "BULL_PUT_SPREAD", "quantity": 65}, inst
    )
    normal = get_strategy_params()
    tightened = replace(normal, credit_stop_loss_pct=normal.credit_stop_loss_pct_high_vol)
    assert tightened.credit_stop_loss_pct < normal.credit_stop_loss_pct

    meta_normal = init_credit_trail_meta(
        option=option,
        instrument=inst,
        action="SELL_BULL_PUT_SPREAD",
        entry_index_price=24000.0,
        params=normal,
    )
    meta_tight = init_credit_trail_meta(
        option=option,
        instrument=inst,
        action="SELL_BULL_PUT_SPREAD",
        entry_index_price=24000.0,
        params=tightened,
    )
    assert meta_tight["stop_loss_pct"] == tightened.credit_stop_loss_pct
    # a smaller stop_pct on the same max loss -> a smaller rupee stop -> exits sooner
    assert meta_tight["stop_loss_rupees"] < meta_normal["stop_loss_rupees"]




def test_credit_uses_credit_eval_not_index_trail() -> None:
    inst = get_instrument("NIFTY")
    trade = {
        "id": "t2",
        "instrument": "NIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "option": {
            "legs": _bull_put_legs(),
            "structure": "BULL_PUT_SPREAD",
            "quantity": 65,
            "net_credit_points": 40.0,
            "trail_meta": init_credit_trail_meta(
                option={
                    "legs": _bull_put_legs(),
                    "structure": "BULL_PUT_SPREAD",
                    "quantity": 65,
                    "net_credit_points": 40.0,
                },
                instrument=inst,
                action="SELL_BULL_PUT_SPREAD",
                entry_index_price=24050.0,
            ),
            "mtm_pnl": 0.0,
        },
        "signal": {"price": 24050.0},
    }
    result = evaluate_open_trade(trade, 23900.0, _dummy_risk())
    assert (
        result.get("credit_exit") is False
        or result.get("trail", {}).get("exit_mode") == "credit_spread"
    )


def _dummy_risk():
    from index_ai.config import RiskSettings

    return RiskSettings(
        trading_mode="PAPER",
        allow_live_trading=False,
        allow_option_buying=True,
        allow_option_selling=True,
        max_losing_trades_per_day=3,
        max_daily_loss_rupees=6000.0,
        trailing_stop_index_points=40.0,
        min_confidence=0.55,
        max_profit_cap_rupees=None,
    )


# --- premium trail on the short leg (NIFTY / BANKNIFTY) ------------------------


def _bn_bear_call(short_now: float, long_now: float = 30.0) -> dict:
    return {
        "legs": [
            {
                "transaction_type": "SELL",
                "option_type": "CALL",
                "strike": 58000,
                "ltp": 300.0,
                "entry_ltp": 300.0,
                "current_ltp": short_now,
                "security_id": 1,
                "segment": "NSE_FNO",
            },
            {
                "transaction_type": "BUY",
                "option_type": "CALL",
                "strike": 58600,
                "ltp": 55.0,
                "entry_ltp": 55.0,
                "current_ltp": long_now,
                "security_id": 2,
                "segment": "NSE_FNO",
            },
        ],
        "structure": "BEAR_CALL_SPREAD",
        "quantity": 30,
        "net_credit_points": 245.0,
        "trail_meta": {"exit_mode": "credit_spread"},
    }


def _bn_trade(option: dict) -> dict:
    return {
        "id": "bn",
        "instrument": "BANKNIFTY",
        "action": "SELL_BEAR_CALL_SPREAD",
        "option": option,
        "signal": {"price": 57900.0, "action": "SELL_BEAR_CALL_SPREAD"},
    }





# --- hedge strike picked by its own premium -----------------------------------


def test_hedge_strike_picks_first_in_band() -> None:
    from index_ai.strategies.option_structures import _pick_hedge_strike

    rows = {
        58000 + 100 * i: {"ce": {"last_price": p, "security_id": i}}
        for i, p in enumerate([250, 180, 120, 80, 55, 35, 20, 12])
    }
    assert _pick_hedge_strike(rows, 58000, 300.0, "ce", 100, +1, (30.0, 80.0, 2200.0)) == 58300


def test_hedge_strike_above_band_when_premiums_gap_over_it() -> None:
    from index_ai.strategies.option_structures import _pick_hedge_strike

    rows = {
        58000: {"ce": {"last_price": 300}},
        58100: {"ce": {"last_price": 90}},
        58200: {"ce": {"last_price": 20}},
    }
    assert _pick_hedge_strike(rows, 58000, 300.0, "ce", 100, +1, (30.0, 80.0, 2200.0)) == 58100


def test_hedge_none_when_short_premium_too_small() -> None:
    from index_ai.strategies.option_structures import _pick_hedge_strike

    rows = {58000: {"ce": {"last_price": 40}}, 58100: {"ce": {"last_price": 10}}}
    assert _pick_hedge_strike(rows, 58000, 40.0, "ce", 100, +1, (30.0, 80.0, 2200.0)) is None


# --- Richard's index trail on sells (2026-09-24): NIFTY 40, BANKNIFTY 100, 1:1 --


def _nifty_bull_put(mtm: float = 0.0) -> dict:
    return {
        "id": "n",
        "instrument": "NIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "signal": {"price": 24050.0, "action": "SELL_BULL_PUT_SPREAD"},
        "option": {"legs": _bull_put_legs(), "structure": "BULL_PUT_SPREAD", "quantity": 65,
                   "net_credit_points": 40.0, "mtm_pnl": mtm},
    }


def _walk(trade: dict, prices: list[float]) -> dict:
    r = {}
    for px in prices:
        r = evaluate_credit_open_trade(trade, px, _dummy_risk())
        trade["option"]["trail_meta"] = r["trail"]
        if r["should_exit"]:
            break
    return r


def test_sell_index_trail_starts_40_below_and_follows_one_for_one() -> None:
    t = _nifty_bull_put()
    r = _walk(t, [24050.0])
    assert r["trail"]["it_stop"] == 24010.0 and not r["should_exit"]
    r = _walk(t, [24120.0, 24100.0])                 # best 24120 -> stop 24080, pullback holds
    assert r["trail"]["it_stop"] == 24080.0 and not r["should_exit"]
    r = _walk(t, [24079.0])
    assert r["should_exit"] and "Index trail" in r["exit_reason"] and "+30 pts" in r["exit_reason"]


def test_sell_index_trail_is_the_exit_not_the_old_rupee_target() -> None:
    # big paper profit, index flat: no rupee "profit target" exit any more
    r = _walk(_nifty_bull_put(mtm=5000.0), [24050.0])
    assert not r["should_exit"]


def test_banknifty_bear_call_mirrors_with_100_points() -> None:
    opt = _bn_bear_call(short_now=300.0)
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, _dummy_risk())
    assert r["trail"]["it_stop"] == 58000.0
    opt["trail_meta"] = r["trail"]
    r = evaluate_credit_open_trade(_bn_trade(opt), 57750.0, _dummy_risk())   # falls 150: good
    assert r["trail"]["it_stop"] == 57850.0 and not r["should_exit"]
    opt["trail_meta"] = r["trail"]
    r = evaluate_credit_open_trade(_bn_trade(opt), 57851.0, _dummy_risk())
    assert r["should_exit"]


def test_max_loss_backstop_still_applies() -> None:
    t = _nifty_bull_put()
    meta = init_credit_trail_meta(option=t["option"], instrument=get_instrument("NIFTY"),
                                  action="SELL_BULL_PUT_SPREAD", entry_index_price=24050.0)
    t["option"]["trail_meta"] = meta
    t["option"]["mtm_pnl"] = -meta["max_loss_rupees"] - 1
    r = evaluate_credit_open_trade(t, 24049.0, _dummy_risk())
    assert r["should_exit"] and "max loss" in r["exit_reason"].lower()
