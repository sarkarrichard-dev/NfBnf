from __future__ import annotations

import pandas as pd

from index_ai.options_oi import OptionOiContext
from index_ai.strategies.strategy_params import reload_strategy_params
from index_ai.strategies.strategy_router import (
    evaluate_dual_opportunities,
    route_intraday_signal,
    trade_lane,
)


def test_trade_lane_buy_and_sell() -> None:
    assert trade_lane("BUY_CALL") == "buy"
    assert trade_lane("SELL_BULL_PUT_SPREAD") == "sell"


def test_dual_lane_auto_evaluates_buy_and_sell(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("REQUIRE_SUPERTREND_ALIGN", "false")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    today = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    dual = evaluate_dual_opportunities(
        today, previous, allow_option_selling=True, allow_option_buying=True
    )
    assert dual.regime.day_bias == "SIDEWAYS"
    assert dual.buy.action in {"NO_TRADE", "BUY_CALL", "BUY_PUT"}
    assert dual.sell.action in {
        "NO_TRADE",
        "SELL_IRON_CONDOR",
        "SELL_BULL_PUT_SPREAD",
        "SELL_BEAR_CALL_SPREAD",
    }


def test_buy_lane_reaches_for_the_real_oi_walls(monkeypatch) -> None:
    """The buy lane must receive the same option-chain OI walls the sell lane
    already uses, and prefer them over the candle-range guess — completing
    wiring that was already in place for the sell lane but never reached the
    buy lane's own evaluate_buy_signal() call."""
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("REQUIRE_SUPERTREND_ALIGN", "false")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    rows = [{"open": 100.0, "high": 101.0, "low": 99.5, "close": 99.8} for _ in range(45)]
    rows.append({"open": 100.0, "high": 100.2, "low": 99.7, "close": 99.9})
    rows.append({"open": 99.9, "high": 100.5, "low": 99.6, "close": 100.4})
    today = pd.DataFrame(rows)

    # no OI context at all — the candle range doesn't put this bar near a
    # level, so nothing fires (matches the pre-fix, OI-blind behaviour)
    without_oi = evaluate_dual_opportunities(
        today, previous, allow_option_selling=False, allow_option_buying=True, oi=None
    )
    assert without_oi.buy.action == "NO_TRADE"
    assert without_oi.buy.sr_source == "candle"

    # a real OI context whose put wall sits right where price already is —
    # the exact same candles now read as "at support" and the buy fires
    oi = OptionOiContext(
        spot=100.4,
        atm_strike=100.0,
        total_call_oi=1,
        total_put_oi=1,
        pcr=1.0,
        max_call_oi_strike=101.0,
        max_put_oi_strike=100.3,
        bias="balanced",
        note="",
        confidence_adjustment=0.0,
    )
    with_oi = evaluate_dual_opportunities(
        today, previous, allow_option_selling=False, allow_option_buying=True, oi=oi
    )
    assert with_oi.buy.action == "BUY_CALL"
    assert with_oi.buy.sr_source == "oi"
    assert "(OI wall)" in with_oi.buy.reason


def test_route_intraday_signal_returns_primary(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    reload_strategy_params()
    previous = pd.DataFrame({"open": [100], "high": [101], "low": [99], "close": [100.5]})
    today = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    signal, regime = route_intraday_signal(today, previous)
    assert regime.day_bias == "SIDEWAYS"
    assert signal.action in {"NO_TRADE", "BUY_CALL", "BUY_PUT", "SELL_IRON_CONDOR"}
