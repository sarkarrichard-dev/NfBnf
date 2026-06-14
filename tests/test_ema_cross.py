from __future__ import annotations

import pandas as pd

from index_ai.ema_cross import analyze_ema_cross, credit_action_for_cross


def _frame_with_cross_down() -> pd.DataFrame:
    """Last bar: fast EMA crosses below slow EMA."""
    n = 22
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "ema_fast": [100.5] * (n - 2) + [100.0, 99.0],
            "ema_slow": [100.0] * (n - 2) + [100.0, 100.5],
        }
    )


def test_detects_bearish_cross_down() -> None:
    cross = analyze_ema_cross(_frame_with_cross_down(), fast=8, slow=20)
    assert cross["ready"] is True
    assert cross["cross"] == "DOWN"
    assert cross["cross_down"] is True
    assert credit_action_for_cross(cross) == "SELL_BEAR_CALL_SPREAD"


def test_no_credit_action_without_cross() -> None:
    flat = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    cross = analyze_ema_cross(flat, fast=8, slow=20)
    assert cross["ready"] is True
    assert cross["cross"] == ""
    assert credit_action_for_cross(cross) is None
