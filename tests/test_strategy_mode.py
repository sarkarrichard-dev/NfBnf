from __future__ import annotations

import pandas as pd

from index_ai.strategies.cpr_regime import analyze_cpr_regime
from index_ai.strategies.ema_cross import analyze_ema_cross
from index_ai.strategies.strategy_mode import pick_auto_credit
from index_ai.strategies.strategy_params import reload_strategy_params
from index_ai.strategies.strategy_router import route_intraday_signal


def _sideways_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    today = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    return today, previous


def test_auto_sideways_flat_ema_picks_iron_condor() -> None:
    today, previous = _sideways_frames()
    frame = today.copy()
    cross = analyze_ema_cross(frame, fast=8, slow=20)
    regime = analyze_cpr_regime(frame, previous)
    action, _, mode = pick_auto_credit(regime, cross, ema_fast=8, ema_slow=20)
    assert regime.day_bias == "SIDEWAYS"
    assert action == "SELL_IRON_CONDOR"
    assert mode == "cpr_sideways"


def test_auto_blocks_bear_call_when_ema_bull_vs_bear_cpr() -> None:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 95, "close": 108},
            {"open": 108, "high": 112, "low": 104, "close": 110},
        ]
    )
    n = 25
    today = pd.DataFrame(
        {
            "open": [90.0] * n,
            "high": [91.0] * n,
            "low": [89.0] * n,
            "close": [90.0] * n,
            "ema_fast": [92.0] * n,
            "ema_slow": [91.0] * n,
        }
    )
    cross = analyze_ema_cross(today, fast=8, slow=20)
    regime = analyze_cpr_regime(today, previous)
    action, reason, mode = pick_auto_credit(regime, cross, ema_fast=8, ema_slow=20)
    assert action is None
    assert mode in {"conflict", "wait"}
    assert "aligned" in reason.lower() or "ema bull" in reason.lower() or "no credit" in reason.lower()


def test_route_auto_intelligent_iron_condor(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("AUTO_INTELLIGENT_ROUTING", "true")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    reload_strategy_params()
    today, previous = _sideways_frames()
    signal, regime = route_intraday_signal(today, previous, allow_option_selling=True)
    assert regime.day_bias == "SIDEWAYS"
    assert signal.action == "SELL_IRON_CONDOR"
    assert signal.strategy_mode == "cpr_sideways"
