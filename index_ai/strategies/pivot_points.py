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
