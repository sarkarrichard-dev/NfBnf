from __future__ import annotations

import pandas as pd

from index_ai.strategies.strategy_params import reload_strategy_params
from index_ai.strategies.strategy_router import evaluate_dual_opportunities, route_intraday_signal, trade_lane


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
    dual = evaluate_dual_opportunities(today, previous, allow_option_selling=True, allow_option_buying=True)
    assert dual.regime.day_bias == "SIDEWAYS"
    assert dual.buy.action in {"NO_TRADE", "BUY_CALL", "BUY_PUT"}
    assert dual.sell.action in {"NO_TRADE", "SELL_IRON_CONDOR", "SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD"}


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
