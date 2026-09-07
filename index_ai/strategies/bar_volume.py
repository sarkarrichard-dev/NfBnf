"""1m bar volume vs recent average — confirms CPR / EMA credit entries."""

from __future__ import annotations

from typing import Any

import pandas as pd


def analyze_bar_volume(
    frame: pd.DataFrame | None,
    *,
    lookback: int = 20,
) -> dict[str, Any]:
    """
    Compare the latest bar volume to the prior-bar average on the spot chart.

    Returns confirms=False when the last bar is materially below recent participation.
    Missing volume column → ready=False (callers should not block on that).
    """
    if frame is None or frame.empty or "volume" not in frame.columns:
        return {"ready": False, "ratio": 1.0}

    # The live frame's last row is the bar still forming this minute — its volume
    # is a fraction of a full bar (10-40s in when the scanner runs) and would fail
    # any ratio gate. Confirm participation on the last *closed* bar instead.
    vol = frame["volume"].astype(float).iloc[:-1]
    if len(vol) < 2:
        return {"ready": False, "ratio": 1.0}

    last = float(vol.iloc[-1])
    prior = vol.iloc[-lookback - 1 : -1]
    if prior.empty:
        return {"ready": False, "ratio": 1.0}

    avg_prior = float(prior.mean())
    if last <= 0 and avg_prior <= 0:
        return {"ready": False, "ratio": 1.0}
    ratio = last / max(avg_prior, 1.0)
    return {
        "ready": True,
        "last_bar_volume": int(last),
        "avg_bar_volume": int(round(avg_prior)),
        "ratio": round(ratio, 3),
    }


def volume_confirms(
    frame: pd.DataFrame | None,
    *,
    min_ratio: float,
    lookback: int = 20,
) -> tuple[bool, dict[str, Any]]:
    """True when volume supports the entry; missing data does not block."""
    stats = analyze_bar_volume(frame, lookback=lookback)
    if not stats.get("ready"):
        return True, stats
    ok = float(stats["ratio"]) >= max(0.0, min_ratio)
    return ok, {**stats, "confirms": ok}
