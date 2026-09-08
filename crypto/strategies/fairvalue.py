"""Fair Value Gaps (3-candle imbalances) on a candle frame.

A **bullish FVG** is a gap the market left when it ran up fast: candle *i*'s low
sits above candle *i-2*'s high, so the zone ``[high[i-2], low[i]]`` was skipped.
Price tends to come back and trade through it. Mirror for a **bearish FVG**.

``find_fvgs`` returns the gaps that are still *live* — no later candle has
*closed* beyond the far edge (a wick into the zone is a retest, not an
invalidation) — newest last, capped at ``max_age_bars`` old. Pure numpy.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def find_fvgs(
    df: pd.DataFrame,
    *,
    atr_val: float,
    min_atr: float = 0.25,
    max_age_bars: int = 24,
) -> list[dict[str, Any]]:
    """Live fair value gaps in ``df``. Each: ``{dir, lo, hi, idx}`` where
    ``dir`` is +1 bullish / -1 bearish, ``[lo, hi]`` is the gap zone, ``idx`` is
    the bar that completed the gap. Only gaps at least ``min_atr * atr_val`` wide
    and formed within the last ``max_age_bars`` bars are returned."""
    n = len(df)
    if n < 3 or atr_val <= 0:
        return []
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    width = max(min_atr * atr_val, 0.0)
    start = max(2, n - max_age_bars)
    out: list[dict[str, Any]] = []
    for i in range(start, n):
        # bullish: low[i] above high[i-2]
        gap = low[i] - h[i - 2]
        if gap >= width:
            lo_, hi_ = h[i - 2], low[i]
            if not np.any(c[i + 1 :] < lo_):  # never closed below → still live
                out.append({"dir": 1, "lo": float(lo_), "hi": float(hi_), "idx": i})
            continue
        # bearish: high[i] below low[i-2]
        gap = low[i - 2] - h[i]
        if gap >= width:
            lo_, hi_ = h[i], low[i - 2]
            if not np.any(c[i + 1 :] > hi_):
                out.append({"dir": -1, "lo": float(lo_), "hi": float(hi_), "idx": i})
    return out


if __name__ == "__main__":  # self-check — one clean bullish gap, one filled
    # bars 3-5: a jump that leaves low[5] > high[3]; later bars stay above it
    highs = [10.0, 10, 10, 11, 13, 15, 16, 16, 16, 16]
    lows = [9.0, 9, 9, 10, 12, 14, 15, 15, 15, 15]
    closes = [9.5, 9.5, 9.5, 10.5, 12.5, 14.5, 15.5, 15.5, 15.5, 15.5]
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-08", periods=10, freq="5min", tz="Asia/Kolkata"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * 10,
        }
    )
    gaps = find_fvgs(frame, atr_val=1.0, min_atr=0.2, max_age_bars=20)
    assert gaps and all(g["dir"] == 1 for g in gaps), gaps
    for g in gaps:  # zone is high[i-2]..low[i], and it is a real gap
        assert g["lo"] < g["hi"] and g["lo"] == highs[g["idx"] - 2] and g["hi"] == lows[g["idx"]], g

    # close the last bar below every gap → all bullish gaps invalidated
    frame.loc[9, ["close", "low"]] = [8.0, 7.5]
    gaps2 = find_fvgs(frame, atr_val=1.0, min_atr=0.2, max_age_bars=20)
    assert not any(g["dir"] == 1 for g in gaps2), gaps2
    print("crypto.strategies.fairvalue self-check ok —", len(gaps), "live then", len(gaps2))
