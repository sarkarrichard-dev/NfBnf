from __future__ import annotations

import pandas as pd

from index_ai.strategies.strategy import cpr_ema_signal, intraday_strategy_signal
from index_ai.strategies.strategy_params import StrategyParams


def _bullish_cpr_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 95, "close": 108},
            {"open": 108, "high": 112, "low": 104, "close": 110},
        ]
    )
    today = pd.DataFrame(
        [{"open": 115 + i, "high": 116 + i, "low": 114 + i, "close": 115 + i} for i in range(25)]
    )
    return today, previous


def test_intraday_blocks_long_when_supertrend_bearish(monkeypatch) -> None:
    today, previous = _bullish_cpr_frames()
    base = cpr_ema_signal(today, previous)
    assert base.action == "BUY_CALL"

    import index_ai.strategies.strategy as strat

    monkeypatch.setattr(
        strat,
        "supertrend_snapshot",
        lambda *_a, **_k: {"ready": True, "direction": -1, "stop": 120.0},
    )
    monkeypatch.setattr(
        strat,
        "detect_breakout",
        lambda *_a, **_k: {"ready": True, "break_res": False, "break_sup": False, "breakout_tag": ""},
    )

    signal = intraday_strategy_signal(today, previous)
    assert signal.action == "NO_TRADE"
    assert "Supertrend" in signal.reason


def test_intraday_boosts_confidence_on_break_res(monkeypatch) -> None:
    today, previous = _bullish_cpr_frames()
    import index_ai.strategies.strategy as strat

    monkeypatch.setattr(
        strat,
        "supertrend_snapshot",
        lambda *_a, **_k: {"ready": True, "direction": 1, "stop": 110.0},
    )
    monkeypatch.setattr(
        strat,
        "detect_breakout",
        lambda *_a, **_k: {
            "ready": True,
            "break_res": True,
            "break_sup": False,
            "breakout_tag": "BREAK_RES",
            "range_high": 114.0,
            "range_low": 100.0,
        },
    )

    base = cpr_ema_signal(today, previous)
    signal = intraday_strategy_signal(today, previous)
    assert signal.action == "BUY_CALL"
    assert signal.breakout_tag == "BREAK_RES"
    assert signal.confidence >= base.confidence


def test_require_breakout_tag_blocks_without_break(monkeypatch) -> None:
    today, previous = _bullish_cpr_frames()
    import index_ai.strategies.strategy as strat

    monkeypatch.setattr(
        strat,
        "supertrend_snapshot",
        lambda *_a, **_k: {"ready": True, "direction": 1, "stop": 110.0},
    )
    monkeypatch.setattr(
        strat,
        "detect_breakout",
        lambda *_a, **_k: {"ready": True, "break_res": False, "break_sup": False, "breakout_tag": ""},
    )

    params = StrategyParams(require_breakout_tag=True)
    signal = intraday_strategy_signal(today, previous, params=params)
    assert signal.action == "NO_TRADE"
