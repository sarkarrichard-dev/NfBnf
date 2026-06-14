from __future__ import annotations

import pandas as pd

from index_ai.strategy_params import reload_strategy_params
from index_ai.strategy_router import route_intraday_signal


def test_auto_routes_sideways_to_iron_condor(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_INTELLIGENT_ROUTING", "true")
    monkeypatch.setenv("AUTO_CREDIT_SIDEWAYS_ONLY", "true")
    monkeypatch.setenv("AUTO_TREND_BUY_FIRST", "true")
    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    today = pd.DataFrame(
        [
            {"open": 99, "high": 101, "low": 98, "close": 100}
            for _ in range(25)
        ]
    )
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    signal, regime = route_intraday_signal(today, previous, allow_option_selling=True)
    assert regime.day_bias == "SIDEWAYS"
    assert signal.action == "SELL_IRON_CONDOR"


def test_buy_style_keeps_directional_logic(monkeypatch) -> None:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 95, "close": 108},
            {"open": 108, "high": 112, "low": 104, "close": 110},
        ]
    )
    today = pd.DataFrame(
        [{"open": 115 + i, "high": 116 + i, "low": 114 + i, "close": 115 + i} for i in range(25)]
    )
    monkeypatch.setenv("STRATEGY_STYLE", "BUY")
    signal, _ = route_intraday_signal(today, previous, allow_option_selling=True)
    assert signal.action in {"BUY_CALL", "NO_TRADE"}


def test_auto_trending_day_prefers_buy_over_credit(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("AUTO_INTELLIGENT_ROUTING", "true")
    monkeypatch.setenv("AUTO_TREND_BUY_FIRST", "true")
    monkeypatch.setenv("AUTO_CREDIT_SIDEWAYS_ONLY", "true")
    monkeypatch.setenv("REQUIRE_BREAKOUT_TAG", "false")
    monkeypatch.setenv("ENTRY_CONFIRMATION_BARS", "2")
    monkeypatch.setenv("MIN_DIRECTIONAL_EMA_SPREAD_PCT", "0.01")
    reload_strategy_params()
    previous = pd.DataFrame(
        {"open": [100], "high": [101], "low": [99], "close": [100.5]}
    )
    rows = []
    for i in range(25):
        close = 102.5 + i * 0.15
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.4,
                "close": close,
            }
        )
    today = pd.DataFrame(rows)
    signal, regime = route_intraday_signal(today, previous, allow_option_selling=True)
    assert regime.day_bias == "TRENDING_BULL"
    assert signal.action == "BUY_CALL"
    assert signal.strategy_mode == "buy"


def test_auto_trending_day_skips_credit_when_sideways_only(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("AUTO_CREDIT_SIDEWAYS_ONLY", "true")
    monkeypatch.setenv("AUTO_TREND_BUY_FIRST", "true")
    monkeypatch.setenv("REQUIRE_BREAKOUT_TAG", "false")
    reload_strategy_params()
    previous = pd.DataFrame(
        {"open": [100], "high": [101], "low": [99], "close": [100.5]}
    )
    today = pd.DataFrame(
        [
            {
                "open": 100.8 + i * 0.02,
                "high": 101.2 + i * 0.02,
                "low": 100.4 + i * 0.02,
                "close": 100.9 + i * 0.02,
            }
            for i in range(25)
        ]
    )
    signal, regime = route_intraday_signal(today, previous, allow_option_selling=True)
    assert regime.day_bias == "TRENDING_BULL"
    assert signal.action != "SELL_BULL_PUT_SPREAD"
