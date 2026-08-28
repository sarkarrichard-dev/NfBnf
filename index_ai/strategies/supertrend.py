"""ATR Supertrend (TradingView-style stepped trend line)."""

from __future__ import annotations

import pandas as pd


def compute_supertrend(
    candles: pd.DataFrame,
    *,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Return dataframe with supertrend line and direction (+1 bull, -1 bear)."""
    df = candles.copy()
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()

    hl2 = (high + low) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(df)
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    st = [0.0] * n
    direction = [1] * n

    final_upper[0] = float(basic_upper.iloc[0])
    final_lower[0] = float(basic_lower.iloc[0])
    st[0] = final_lower[0]
    direction[0] = 1

    for i in range(1, n):
        bu = float(basic_upper.iloc[i])
        bl = float(basic_lower.iloc[i])
        c = float(close.iloc[i])
        fu_prev = final_upper[i - 1]
        fl_prev = final_lower[i - 1]

        final_upper[i] = bu if bu < fu_prev or float(close.iloc[i - 1]) > fu_prev else fu_prev
        final_lower[i] = bl if bl > fl_prev or float(close.iloc[i - 1]) < fl_prev else fl_prev

        if direction[i - 1] == 1:
            if c < final_lower[i]:
                direction[i] = -1
                st[i] = final_upper[i]
            else:
                direction[i] = 1
                st[i] = final_lower[i]
        else:
            if c > final_upper[i]:
                direction[i] = 1
                st[i] = final_lower[i]
            else:
                direction[i] = -1
                st[i] = final_upper[i]

    df["supertrend"] = st
    df["supertrend_direction"] = direction
    return df


def supertrend_snapshot(candles: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> dict:
    if len(candles) < period + 2:
        return {"direction": 0, "stop": 0.0, "ready": False}
    frame = compute_supertrend(candles, period=period, multiplier=multiplier)
    row = frame.iloc[-1]
    prev = frame.iloc[-2]
    return {
        "ready": True,
        "direction": int(row["supertrend_direction"]),
        "stop": float(row["supertrend"]),
        "prev_direction": int(prev["supertrend_direction"]),
        "flipped": int(row["supertrend_direction"]) != int(prev["supertrend_direction"]),
    }
