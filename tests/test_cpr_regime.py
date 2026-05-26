from __future__ import annotations

import pandas as pd
import pytest

from index_ai.cpr_regime import analyze_cpr_regime
from index_ai.strategy_params import reload_strategy_params


@pytest.fixture(autouse=True)
def _reset_strategy_params() -> None:
    reload_strategy_params()
    yield
    reload_strategy_params()


def test_wide_cpr_classifies_sideways_bias() -> None:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    today = pd.DataFrame(
        [
            {"open": 99, "high": 101, "low": 98, "close": 100, "ema_fast": 100, "ema_slow": 100}
            for _ in range(25)
        ]
    )
    regime = analyze_cpr_regime(today, previous, price=100.0, ema_fast=100.0, ema_slow=100.0)
    assert regime.width_class == "WIDE"
    assert regime.day_bias == "SIDEWAYS"


def test_narrow_cpr_above_tc_trending_bull() -> None:
    previous = pd.DataFrame(
        {"open": [100], "high": [101], "low": [99], "close": [100.5]}
    )
    today = pd.DataFrame(
        [
            {
                "open": 102 + i * 0.1,
                "high": 103 + i * 0.1,
                "low": 101 + i * 0.1,
                "close": 102.5 + i * 0.1,
                "ema_fast": 103.0,
                "ema_slow": 101.0,
            }
            for i in range(25)
        ]
    )
    regime = analyze_cpr_regime(today, previous)
    assert regime.width_class == "NARROW"
    assert regime.day_bias == "TRENDING_BULL"
