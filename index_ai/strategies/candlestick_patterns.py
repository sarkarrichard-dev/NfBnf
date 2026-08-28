"""Candlestick pattern detection at candle-based support / resistance."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategies.breakout import detect_breakout
from index_ai.strategies.candlestick_sr import intraday_candle_trend, swing_levels


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _upper_wick(row: pd.Series) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def _lower_wick(row: pd.Series) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def _bullish_bar(row: pd.Series) -> bool:
    return float(row["close"]) >= float(row["open"])


def detect_candlestick_setup(
    frame: pd.DataFrame,
    *,
    sr_lookback: int = 30,
    trend_lookback: int = 15,
    breakout_lookback: int = 20,
) -> dict[str, Any]:
    """
    Scan last bars for reversal / continuation patterns near S/R.
    Returns pattern name, direction (bull/bear/none), and context.
    """
    if frame is None or len(frame) < 3:
        return {"ready": False, "pattern": "", "direction": "none"}

    sr = swing_levels(frame, lookback=sr_lookback)
    if not sr.get("ready"):
        return {"ready": False, "pattern": "", "direction": "none"}

    trend = intraday_candle_trend(frame, lookback=trend_lookback)
    br = detect_breakout(frame, lookback=breakout_lookback)
    curr = frame.iloc[-1]
    prev = frame.iloc[-2]
    body = _body(curr)
    prev_body = max(_body(prev), 0.01)

    pattern = ""
    direction = "none"
    reason_parts: list[str] = []

    # Bullish engulfing at support
    if (
        sr.get("near_support")
        and not _bullish_bar(prev)
        and _bullish_bar(curr)
        and float(curr["close"]) > float(prev["open"])
        and float(curr["open"]) <= float(prev["close"])
        and body >= prev_body * 0.9
    ):
        pattern = "bullish_engulfing"
        direction = "bull"
        reason_parts.append(f"Bullish engulfing at support {sr['support']:.0f}")

    # Bearish engulfing at resistance
    elif (
        sr.get("near_resistance")
        and _bullish_bar(prev)
        and not _bullish_bar(curr)
        and float(curr["close"]) < float(prev["open"])
        and float(curr["open"]) >= float(prev["close"])
        and body >= prev_body * 0.9
    ):
        pattern = "bearish_engulfing"
        direction = "bear"
        reason_parts.append(f"Bearish engulfing at resistance {sr['resistance']:.0f}")

    # Hammer at support
    elif sr.get("near_support") and _lower_wick(curr) >= body * 2 and _upper_wick(curr) <= body * 0.5:
        pattern = "hammer"
        direction = "bull"
        reason_parts.append(f"Hammer at support {sr['support']:.0f}")

    # Shooting star at resistance
    elif (
        sr.get("near_resistance")
        and _upper_wick(curr) >= body * 2
        and _lower_wick(curr) <= body * 0.5
    ):
        pattern = "shooting_star"
        direction = "bear"
        reason_parts.append(f"Shooting star at resistance {sr['resistance']:.0f}")

    # Breakout continuation (mid-day trend)
    elif br.get("break_res") and trend in {"UP", "RANGE"}:
        pattern = "breakout_resistance"
        direction = "bull"
        reason_parts.append(
            f"Breakout above resistance {br.get('range_high', sr['resistance']):.0f} "
            f"(intraday trend {trend})"
        )
    elif br.get("break_sup") and trend in {"DOWN", "RANGE"}:
        pattern = "breakdown_support"
        direction = "bear"
        reason_parts.append(
            f"Breakdown below support {br.get('range_low', sr['support']):.0f} "
            f"(intraday trend {trend})"
        )

    # Pullback in established intraday trend
    elif trend == "UP" and sr.get("near_support") and _bullish_bar(curr):
        pattern = "trend_pullback_long"
        direction = "bull"
        reason_parts.append(f"Intraday UP trend — bullish bar at support {sr['support']:.0f}")
    elif trend == "DOWN" and sr.get("near_resistance") and not _bullish_bar(curr):
        pattern = "trend_pullback_short"
        direction = "bear"
        reason_parts.append(
            f"Intraday DOWN trend — bearish bar at resistance {sr['resistance']:.0f}"
        )

    return {
        "ready": bool(pattern),
        "pattern": pattern,
        "direction": direction,
        "intraday_trend": trend,
        "support": sr.get("support"),
        "resistance": sr.get("resistance"),
        "near_support": sr.get("near_support"),
        "near_resistance": sr.get("near_resistance"),
        "breakout": br,
        "reason": " ".join(reason_parts),
    }
