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


def test_credit_profit_target_exit_when_trail_disabled() -> None:
    inst = get_instrument("NIFTY")
    legs = _bull_put_legs()
    meta = init_credit_trail_meta(
        option={
            "legs": legs,
            "structure": "BULL_PUT_SPREAD",
            "quantity": 65,
            "net_credit_points": 40.0,
        },
        instrument=inst,
        action="SELL_BULL_PUT_SPREAD",
        entry_index_price=24050.0,
    )
    meta["enable_profit_trail"] = False
    meta["use_profit_trail"] = False
    option = {
        "legs": legs,
        "structure": "BULL_PUT_SPREAD",
        "quantity": 65,
        "net_credit_points": 40.0,
        "trail_meta": meta,
        "mtm_pnl": 1400.0,
    }
    trade = {
        "id": "t1",
        "instrument": "NIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "option": option,
        "signal": {"price": 24050.0, "action": "SELL_BULL_PUT_SPREAD"},
    }
    assert meta["profit_target_rupees"] == round(40 * 65 * 0.5, 2)
    result = evaluate_credit_open_trade(trade, 24050.0, _dummy_risk())
    assert result["should_exit"] is True
    assert "profit target" in (result.get("exit_reason") or "").lower()


def test_credit_profit_trail_exits_on_giveback() -> None:
    inst = get_instrument("NIFTY")
    legs = _bull_put_legs()
    meta = init_credit_trail_meta(
        option={
            "legs": legs,
            "structure": "BULL_PUT_SPREAD",
            "quantity": 65,
            "net_credit_points": 40.0,
        },
        instrument=inst,
        action="SELL_BULL_PUT_SPREAD",
        entry_index_price=24050.0,
    )
    meta["peak_mtm_pnl"] = 2000.0
    meta["profit_trail_armed"] = True
    meta["enable_profit_trail"] = True
    meta["use_profit_trail"] = True
    option = {
        "legs": legs,
        "structure": "BULL_PUT_SPREAD",
        "quantity": 65,
        "net_credit_points": 40.0,
        "trail_meta": meta,
        "mtm_pnl": 800.0,
    }
    trade = {
        "id": "t1b",
        "instrument": "NIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "option": option,
        "signal": {"price": 24050.0, "action": "SELL_BULL_PUT_SPREAD"},
    }
    result = evaluate_credit_open_trade(trade, 24050.0, _dummy_risk())
    assert result["should_exit"] is True
    assert "profit trail" in (result.get("exit_reason") or "").lower()


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


def test_premium_trail_hard_stop_before_target() -> None:
    opt = _bn_bear_call(short_now=405.0, long_now=70.0)  # +105 pts against
    opt["mtm_pnl"] = -3200.0
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, _dummy_risk())
    assert r["should_exit"] and "Hard stop" in (r["exit_reason"] or "")


def test_premium_trail_arms_at_quarter_then_trails_off_best() -> None:
    risk = _dummy_risk()
    opt = _bn_bear_call(short_now=225.0)  # 300 -> 225 = quarter target
    opt["mtm_pnl"] = 600.0
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, risk)
    assert not r["should_exit"] and r["trail"]["pt_target_hit"]

    opt = _bn_bear_call(short_now=150.0)  # best premium now 150
    opt["trail_meta"] = r["trail"]
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, risk)
    assert not r["should_exit"]

    opt = _bn_bear_call(short_now=185.0)  # +35 bounce off 150 -> exit
    opt["trail_meta"] = r["trail"]
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, risk)
    assert r["should_exit"] and "Trailing exit" in (r["exit_reason"] or "")


def test_premium_trail_falls_back_to_rupee_logic_without_current_ltp() -> None:
    # legs carry no current_ltp (unit path) -> legacy rupee target still applies
    opt = _bn_bear_call(short_now=225.0)
    for leg in opt["legs"]:
        leg.pop("current_ltp")
    meta = init_credit_trail_meta(
        option=opt,
        instrument=get_instrument("BANKNIFTY"),
        action="SELL_BEAR_CALL_SPREAD",
        entry_index_price=57900.0,
    )
    meta["use_profit_trail"] = False
    opt["trail_meta"] = meta
    opt["mtm_pnl"] = meta["profit_target_rupees"] + 1
    r = evaluate_credit_open_trade(_bn_trade(opt), 57900.0, _dummy_risk())
    assert r["should_exit"] and "profit target" in (r["exit_reason"] or "").lower()


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
