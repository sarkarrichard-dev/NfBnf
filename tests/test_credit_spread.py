from __future__ import annotations

from index_ai.credit_spread import (
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
        {"transaction_type": "SELL", "option_type": "PUT", "strike": 24000, "ltp": 80.0},
        {"transaction_type": "BUY", "option_type": "PUT", "strike": 23900, "ltp": 40.0},
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


def test_credit_profit_target_exit() -> None:
    inst = get_instrument("NIFTY")
    legs = _bull_put_legs()
    option = {
        "legs": legs,
        "structure": "BULL_PUT_SPREAD",
        "quantity": 65,
        "net_credit_points": 40.0,
        "trail_meta": init_credit_trail_meta(
            option={"legs": legs, "structure": "BULL_PUT_SPREAD", "quantity": 65, "net_credit_points": 40.0},
            instrument=inst,
            action="SELL_BULL_PUT_SPREAD",
            entry_index_price=24050.0,
        ),
        "mtm_pnl": 1400.0,
    }
    trade = {
        "id": "t1",
        "instrument": "NIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "option": option,
        "signal": {"price": 24050.0, "action": "SELL_BULL_PUT_SPREAD"},
    }
    meta = option["trail_meta"]
    assert meta["profit_target_rupees"] == round(40 * 65 * 0.5, 2)
    result = evaluate_credit_open_trade(trade, 24050.0, _dummy_risk())
    assert result["should_exit"] is True
    assert "profit target" in (result.get("exit_reason") or "").lower()


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
    assert result.get("credit_exit") is False or result.get("trail", {}).get("exit_mode") == "credit_spread"


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
