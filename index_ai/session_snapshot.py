"""Spot chart + CPR/EMA/volume metrics for pre-open analysis and briefs."""

from __future__ import annotations

from typing import Any

import pandas as pd


def spot_session_metrics(today: pd.DataFrame, *, signal: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
    """Summarize today’s index candles: volume, price vs CPR, EMA alignment."""
    if today is None or today.empty:
        return {"bars": 0}

    frame = today.copy()
    price = float(signal.get("price") or frame.iloc[-1].get("close") or 0)
    pivot = float(signal.get("pivot") or regime.get("pivot") or 0)
    bc = float(signal.get("bc") or regime.get("bc") or 0)
    tc = float(signal.get("tc") or regime.get("tc") or 0)
    ema_fast = float(signal.get("ema_fast") or frame.iloc[-1].get("ema_fast") or 0)
    ema_slow = float(signal.get("ema_slow") or frame.iloc[-1].get("ema_slow") or 0)

    vol_col = frame["volume"] if "volume" in frame.columns else None
    session_volume = int(vol_col.sum()) if vol_col is not None else 0
    last_bar_volume = int(vol_col.iloc[-1]) if vol_col is not None and len(vol_col) else 0
    avg_bar_volume = round(session_volume / max(len(frame), 1))

    if price > tc:
        cpr_position = "above_tc"
    elif price < bc:
        cpr_position = "below_bc"
    else:
        cpr_position = "inside_cpr"

    if ema_fast > ema_slow:
        ema_bias = "bullish"
    elif ema_fast < ema_slow:
        ema_bias = "bearish"
    else:
        ema_bias = "flat"

    return {
        "bars": len(frame),
        "spot": round(price, 2),
        "session_volume": session_volume,
        "last_bar_volume": last_bar_volume,
        "avg_bar_volume": avg_bar_volume,
        "pivot": round(pivot, 2),
        "bc": round(bc, 2),
        "tc": round(tc, 2),
        "cpr_position": cpr_position,
        "cpr_width_pct": regime.get("width_pct"),
        "cpr_width_class": regime.get("width_class"),
        "cpr_day_bias": regime.get("day_bias"),
        "cpr_virgin": regime.get("virgin_cpr"),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "ema_spread": round(ema_fast - ema_slow, 2),
        "ema_bias": ema_bias,
    }
