"""Spot EMA alignment and crossover detection for directional credit entries."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategy import add_indicators


def min_ema_bars(slow_period: int) -> int:
    """Minimum candles needed for slow EMA plus one prior bar for cross detection."""
    return max(int(slow_period) + 2, 3)


def analyze_ema_cross(
    frame: pd.DataFrame,
    *,
    fast: int,
    slow: int,
) -> dict[str, Any]:
    """
    Inspect the last two bars for EMA alignment and a fresh crossover.

    cross UP: prior fast ≤ slow and current fast > slow (bullish cross).
    cross DOWN: prior fast ≥ slow and current fast < slow (bearish cross).
    """
    need = min_ema_bars(slow)
    if frame is None or len(frame) < need:
        return {
            "ready": False,
            "ema_fast": 0.0,
            "ema_slow": 0.0,
            "aligned": "",
            "cross": "",
            "cross_up": False,
            "cross_down": False,
        }

    df = (
        frame
        if "ema_fast" in frame.columns and "ema_slow" in frame.columns
        else add_indicators(frame, fast=fast, slow=slow)
    )
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    pf, ps = float(prev["ema_fast"]), float(prev["ema_slow"])
    cf, cs = float(curr["ema_fast"]), float(curr["ema_slow"])

    cross_up = pf <= ps and cf > cs
    cross_down = pf >= ps and cf < cs

    if cf > cs:
        aligned = "bull"
    elif cf < cs:
        aligned = "bear"
    else:
        aligned = "flat"

    if cross_up:
        cross = "UP"
    elif cross_down:
        cross = "DOWN"
    else:
        cross = ""

    return {
        "ready": True,
        "ema_fast": cf,
        "ema_slow": cs,
        "aligned": aligned,
        "cross": cross,
        "cross_up": cross_up,
        "cross_down": cross_down,
    }


def credit_action_for_cross(cross: dict[str, Any]) -> str | None:
    """Directional hedged credit from a fresh spot EMA cross only."""
    if not cross.get("ready"):
        return None
    if cross.get("cross_down"):
        return "SELL_BEAR_CALL_SPREAD"
    if cross.get("cross_up"):
        return "SELL_BULL_PUT_SPREAD"
    return None
