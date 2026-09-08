"""Standard (floor-trader) daily pivot points for the crypto lane.

The crypto day starts 00:00 UTC (= 05:30 IST, the "BTC day" the video anchors
to). Today's pivots are computed from *yesterday's* UTC-day high / low / close
and hold for the whole day — a leading level, known at the open.

    P  = (H + L + C) / 3
    R1 = 2P − L        S1 = 2P − H
    R2 = P + (H − L)   S2 = P − (H − L)
    R3 = H + 2(P − L)  S3 = L − 2(P − H)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _prev_utc_day_hlc(df: pd.DataFrame) -> tuple[float, float, float] | None:
    """(high, low, close) of the most recent *complete* UTC calendar day in df."""
    if df is None or len(df) < 2:
        return None
    ts = pd.to_datetime(df["datetime"], utc=True)
    day = ts.dt.floor("1D").to_numpy()
    today = day[-1]
    prev_mask = day < today
    if not prev_mask.any():
        return None
    prev_day = day[prev_mask][-1]
    sel = day == prev_day
    h = float(df["high"].to_numpy(float)[sel].max())
    low = float(df["low"].to_numpy(float)[sel].min())
    c = float(df["close"].to_numpy(float)[sel][-1])
    return h, low, c


def standard_pivots(df: pd.DataFrame) -> dict[str, float]:
    """{'P','R1','R2','R3','S1','S2','S3'} from the previous UTC day, or {} if
    df doesn't yet span a full prior day."""
    hlc = _prev_utc_day_hlc(df)
    if hlc is None:
        return {}
    h, low, c = hlc
    rng = h - low
    p = (h + low + c) / 3.0
    return {
        "P": p,
        "R1": 2 * p - low, "S1": 2 * p - h,
        "R2": p + rng, "S2": p - rng,
        "R3": h + 2 * (p - low), "S3": low - 2 * (h - p),
    }


def nearest_pivot_above(price: float, piv: dict[str, float]) -> tuple[str, float] | None:
    above = [(k, v) for k, v in piv.items() if v > price]
    return min(above, key=lambda kv: kv[1]) if above else None


def nearest_pivot_below(price: float, piv: dict[str, float]) -> tuple[str, float] | None:
    below = [(k, v) for k, v in piv.items() if v < price]
    return max(below, key=lambda kv: kv[1]) if below else None


def crossed_up(prev_close: float, last_close: float, level: float) -> bool:
    return prev_close <= level < last_close


def crossed_down(prev_close: float, last_close: float, level: float) -> bool:
    return prev_close >= level > last_close


if __name__ == "__main__":  # self-check
    n = 600  # ~2 days of 5m
    idx = pd.date_range("2026-09-06", periods=n, freq="5min", tz="UTC")
    px = 100.0 + np.sin(np.linspace(0, 12, n)) * 5
    df = pd.DataFrame({"datetime": idx, "open": px, "high": px + 1, "low": px - 1, "close": px})
    piv = standard_pivots(df)
    assert set(piv) == {"P", "R1", "R2", "R3", "S1", "S2", "S3"}, piv
    assert piv["S1"] < piv["P"] < piv["R1"] and piv["S3"] < piv["S2"] < piv["S1"], piv
    assert nearest_pivot_above(piv["P"], piv)[0] == "R1"
    assert nearest_pivot_below(piv["P"], piv)[0] == "S1"
    assert crossed_up(piv["R1"] - 0.01, piv["R1"] + 0.01, piv["R1"])
    assert not crossed_up(piv["R1"] + 1, piv["R1"] + 2, piv["R1"])
    # a frame that doesn't span a prior day → no pivots
    short = df.iloc[-10:].reset_index(drop=True)
    assert standard_pivots(short) == {}
    print("crypto.strategies.pivots self-check ok —", {k: round(v, 2) for k, v in piv.items()})
