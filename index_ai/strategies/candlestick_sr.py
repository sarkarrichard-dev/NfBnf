"""Support / resistance from recent 1m candles (not CPR)."""

from __future__ import annotations

from typing import Any

import pandas as pd

TOL_PCT = 0.0015


def near_level(close: float, support: float, resistance: float) -> tuple[bool, bool]:
    """(near_support, near_resistance) — same proximity tolerance ``swing_levels``
    uses, factored out so callers can apply it to other levels (e.g. OI walls)."""
    near_support = abs(close - support) / max(close, 1.0) <= TOL_PCT or close <= support * (
        1 + TOL_PCT
    )
    near_resistance = abs(close - resistance) / max(
        close, 1.0
    ) <= TOL_PCT or close >= resistance * (1 - TOL_PCT)
    return near_support, near_resistance


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

    near_support, near_resistance = near_level(close, support, resistance)

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
