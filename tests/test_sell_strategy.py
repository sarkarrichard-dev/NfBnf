from __future__ import annotations

import pandas as pd

from index_ai.strategies.sell_strategy import _trend15_block
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.strategies.strategy_router import evaluate_dual_opportunities


def test_trend15_block_direction_and_swing() -> None:
    cfg = get_strategy_params()
    up = {"ready": True, "direction": 1, "structure": "UP", "swing_high": 110.0, "swing_low": 90.0}
    down = {
        "ready": True,
        "direction": -1,
        "structure": "DOWN",
        "swing_high": 110.0,
        "swing_low": 90.0,
    }

    # aligned -> no block
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 100.0, up, cfg) is None
    assert _trend15_block("SELL_BEAR_CALL_SPREAD", 100.0, down, cfg) is None
    # opposed direction -> block
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 100.0, down, cfg) is not None
    assert _trend15_block("SELL_BEAR_CALL_SPREAD", 100.0, up, cfg) is not None
    # price broke the 15m swing the credit leans on -> block
    assert "support" in _trend15_block("SELL_BULL_PUT_SPREAD", 89.0, up, cfg)
    assert "resistance" in _trend15_block("SELL_BEAR_CALL_SPREAD", 111.0, down, cfg)
    # not ready / disabled -> never blocks
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 89.0, {"ready": False}, cfg) is None


def test_dual_opportunities_split_sell_frame_populates_sell_regime(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    from index_ai.strategies.strategy_params import reload_strategy_params

    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    buy_1m = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    sell_5m = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-25 09:15", periods=30, freq="5min"),
            "open": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "close": [100.0] * 30,
        }
    )
    trend_15m = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-24 09:15", periods=40, freq="15min"),
            "open": [100.0] * 40,
            "high": [101.0] * 40,
            "low": [99.0] * 40,
            "close": [100.0] * 40,
        }
    )
    dual = evaluate_dual_opportunities(buy_1m, previous, sell_today=sell_5m, sell_trend15=trend_15m)
    assert dual.sell_regime is not None
    assert dual.sell.action in {
        "NO_TRADE",
        "SELL_BULL_PUT_SPREAD",
        "SELL_BEAR_CALL_SPREAD",
        "SELL_IRON_CONDOR",
    }
