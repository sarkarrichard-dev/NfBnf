"""Classic floor pivot points (PP, R1, S1) from prior session OHLC — Theta Gainers / TradingView standard."""

from __future__ import annotations

import pandas as pd


def classic_pivot_levels(previous_day: pd.DataFrame) -> tuple[float, float, float, float]:
    """
    Standard pivots from previous session:
    PP = (H + L + C) / 3
    R1 = 2 * PP - L
    S1 = 2 * PP - H
    """
    high = float(previous_day["high"].max())
    low = float(previous_day["low"].min())
    close = float(previous_day["close"].iloc[-1])
    pp = (high + low + close) / 3.0
    r1 = 2.0 * pp - low
    s1 = 2.0 * pp - high
    return round(pp, 2), round(r1, 2), round(s1, 2), round(close, 2)


def classic_pivot_map(previous_day: pd.DataFrame) -> dict[str, float]:
    """Full classic floor pivots — PP, R1-R3, S1-S3 — for target/stop reference."""
    high = float(previous_day["high"].max())
    low = float(previous_day["low"].min())
    close = float(previous_day["close"].iloc[-1])
    rng = high - low
    pp = (high + low + close) / 3.0
    return {
        "PP": round(pp, 2),
        "R1": round(2 * pp - low, 2),
        "R2": round(pp + rng, 2),
        "R3": round(high + 2 * (pp - low), 2),
        "S1": round(2 * pp - high, 2),
        "S2": round(pp - rng, 2),
        "S3": round(low - 2 * (high - pp), 2),
    }
