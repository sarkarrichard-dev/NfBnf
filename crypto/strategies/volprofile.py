"""Session volume profile — POC / value area / shape, from OHLCV candles.

Each candle's volume is spread evenly across the price bins its high-low range
touches (a cheap approximation of a real tick/footprint profile). The value area
is the smallest contiguous band around the POC holding ``va_pct`` of the volume.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Profile:
    poc: float
    vah: float          # value-area high
    val: float          # value-area low
    skew: float          # (poc - mid) / range, -1..1 — where the POC sits in the range
    balanced: bool


def profile(frame: pd.DataFrame, *, bins: int = 30, va_pct: float = 0.70) -> Profile | None:
    if len(frame) < 5:
        return None
    hi = frame["high"].astype(float).to_numpy()
    lo = frame["low"].astype(float).to_numpy()
    vol = frame["volume"].astype(float).clip(lower=0.0).to_numpy()
    top, bot = float(hi.max()), float(lo.min())
    if not np.isfinite(top) or top <= bot:
        return None

    edges = np.linspace(bot, top, bins + 1)
    centres = (edges[:-1] + edges[1:]) / 2.0
    hist = np.zeros(bins)
    for h, lw, v in zip(hi, lo, vol):
        if v <= 0 or h <= lw:
            i = min(bins - 1, max(0, int((h - bot) / (top - bot) * bins)))
            hist[i] += max(v, 1e-9)
            continue
        i0 = max(0, int((lw - bot) / (top - bot) * bins))
        i1 = min(bins - 1, int((h - bot) / (top - bot) * bins))
        hist[i0 : i1 + 1] += v / (i1 - i0 + 1)

    total = hist.sum()
    if total <= 0:
        return None
    poc_i = int(hist.argmax())
    poc = float(centres[poc_i])

    # grow a window out from the POC until it holds va_pct of the volume
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    target = va_pct * total
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1.0
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    val, vah = float(centres[lo_i]), float(centres[hi_i])

    rng = top - bot
    skew = (poc - (top + bot) / 2.0) / rng * 2.0 if rng else 0.0
    va_centre_skew = ((vah + val) / 2.0 - (top + bot) / 2.0) / rng * 2.0 if rng else 0.0
    peakedness = hist[poc_i] / (hist.mean() + 1e-12)  # bell has a clear central peak
    va_width = (hi_i - lo_i + 1) / bins                # bell's value area is compact
    balanced = (
        abs(skew) <= 0.35
        and abs(va_centre_skew) <= 0.35
        and peakedness >= 1.8
        and va_width <= 0.75
    )
    return Profile(poc=poc, vah=vah, val=val, skew=round(skew, 3), balanced=balanced)


if __name__ == "__main__":  # self-check
    # a symmetric bell around 100 → balanced, POC ~100
    rng = np.random.default_rng(0)
    px = 100 + rng.normal(0, 1.0, 400)
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-09-01", periods=400, freq="15min", tz="UTC"),
        "high": px + 0.3, "low": px - 0.3, "close": px, "open": px,
        "volume": np.abs(rng.normal(10, 2, 400)),
    })
    p = profile(df)
    assert p and abs(p.poc - 100) < 1.5 and p.balanced, p
    assert p.val < p.poc < p.vah

    # a one-way ramp → POC pinned near one end, not balanced
    ramp = np.linspace(100, 140, 400)
    df2 = df.assign(high=ramp + 0.3, low=ramp - 0.3, close=ramp, open=ramp)
    p2 = profile(df2)
    assert p2 and not p2.balanced, p2
    print("crypto.strategies.volprofile self-check ok —", p, "|", p2)
