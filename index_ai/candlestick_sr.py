"""Support / resistance from recent 1m candles (not CPR)."""

from __future__ import annotations

from typing import Any

import pandas as pd


def swing_levels(
    frame: pd.DataFrame,
    *,
    lookback: int = 30,
) -> dict[str, Any]:
    """Resistance / support from prior-bar range and session extremes."""
    if frame is None or len(frame) < 3:
        return {"ready": False}

    work = frame.tail(max(lookback + 1, 5))
    prior = work.iloc[:-1]
    last = work.iloc[-1]
    close = float(last["close"])

    range_high = float(prior["high"].max())
    range_low = float(prior["low"].min())
    session_high = float(work["high"].max())
    session_low = float(work["low"].min())

    resistance = max(range_high, session_high)
    support = min(range_low, session_low)

    tol_pct = 0.0015
    near_support = abs(close - support) / max(close, 1.0) <= tol_pct or close <= support * (1 + tol_pct)
    near_resistance = abs(close - resistance) / max(close, 1.0) <= tol_pct or close >= resistance * (1 - tol_pct)

    return {
        "ready": True,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "range_high": round(range_high, 2),
        "range_low": round(range_low, 2),
        "session_high": round(session_high, 2),
        "session_low": round(session_low, 2),
        "near_support": near_support,
        "near_resistance": near_resistance,
        "close": round(close, 2),
    }


def intraday_candle_trend(
    frame: pd.DataFrame,
    *,
    lookback: int = 15,
) -> str:
    """
    Mid-session trend from candle structure (HH/HL vs LH/LL).
    Returns UP, DOWN, or RANGE — independent of CPR day bias.
    """
    if frame is None or len(frame) < lookback + 1:
        return "RANGE"

    segment = frame.tail(lookback)
    highs = segment["high"].astype(float)
    lows = segment["low"].astype(float)
    mid = lookback // 2
    first_high = float(highs.iloc[:mid].max())
    second_high = float(highs.iloc[mid:].max())
    first_low = float(lows.iloc[:mid].min())
    second_low = float(lows.iloc[mid:].min())

    hh = second_high > first_high
    hl = second_low > first_low
    lh = second_high < first_high
    ll = second_low < first_low

    if hh and hl:
        return "UP"
    if lh and ll:
        return "DOWN"
    if hh and not ll:
        return "UP"
    if ll and not hh:
        return "DOWN"
    return "RANGE"
