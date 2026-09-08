"""Renko brick direction from a close series — a plain price-move filter.

Not a full Renko chart: we only need "which way is the last brick pointing", used
as a trend-agreement gate in ``candle_renko``. ATR-sized bricks come from the
caller (``indicators.atr``)."""

from __future__ import annotations

import pandas as pd


def brick_dir(close: pd.Series, brick: float) -> int:
    """+1 if the most recent completed brick is up, -1 if down, 0 if none yet.

    Anchors on the first close and walks forward, snapping the anchor by whole
    bricks whenever price has travelled at least one brick from it."""
    if brick <= 0 or len(close) < 2:
        return 0
    s = close.to_numpy(dtype=float) if hasattr(close, "to_numpy") else [float(x) for x in close]
    anchor = float(s[0])
    d = 0
    for px in s[1:]:
        move = px - anchor
        n = int(abs(move) // brick)
        if n >= 1:
            d = 1 if move > 0 else -1
            anchor += d * n * brick
    return d


if __name__ == "__main__":  # self-check
    up = pd.Series([100 + i for i in range(20)])
    down = pd.Series([100 - i for i in range(20)])
    flat = pd.Series([100.0] * 20)
    assert brick_dir(up, 3.0) == 1
    assert brick_dir(down, 3.0) == -1
    assert brick_dir(flat, 3.0) == 0
    assert brick_dir(up, 0.0) == 0
    # a reversal: up 30 then back down 30 → last brick is down
    v = pd.Series([100 + i for i in range(30)] + [130 - i for i in range(30)])
    assert brick_dir(v, 5.0) == -1
    print("crypto.strategies.renko self-check ok")
