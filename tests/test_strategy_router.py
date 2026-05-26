from __future__ import annotations

import pandas as pd

from index_ai.strategy_router import route_intraday_signal


def test_auto_routes_sideways_to_iron_condor(monkeypatch) -> None:
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
