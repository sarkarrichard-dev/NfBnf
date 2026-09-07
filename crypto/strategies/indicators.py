"""Small indicator helpers not already in ``index_ai`` — EMA, anchored VWAP,
closing-basis swing pivots, and crossover tests. Pure pandas."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.astype(float).ewm(span=length, adjust=False).mean()


def anchored_vwap(df: pd.DataFrame, *, anchor: str = "1D") -> pd.Series:
    """Volume-weighted average of hlc3, restarting each ``anchor`` period
    (default the UTC day — the 'BTC day' the Pine strategy anchors to).
    Falls back to a plain cumulative mean when there is no volume."""
    hlc3 = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    vol = df["volume"].astype(float).clip(lower=0.0)
    grp = pd.to_datetime(df["datetime"]).dt.floor(anchor)
    pv = (hlc3 * vol).groupby(grp).cumsum()
    vv = vol.groupby(grp).cumsum()
    out = pv / vv.replace(0.0, pd.NA)
    return out.fillna(hlc3.groupby(grp).expanding().mean().reset_index(level=0, drop=True))


def pivot_high(series: pd.Series, left: int, right: int) -> pd.Series:
    """Confirmed swing high on a closing basis. Non-NA at bar ``i`` carries the
    pivot value that formed ``right`` bars earlier (matches Pine ``ta.pivothigh``)."""
    s = series.astype(float)
    n = len(s)
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    for i in range(left, n - right):
        window = s.iloc[i - left : i + right + 1]
        if s.iloc[i] == window.max() and (window == s.iloc[i]).sum() == 1:
            out.iloc[i + right] = s.iloc[i]
    return out


def pivot_low(series: pd.Series, left: int, right: int) -> pd.Series:
    s = series.astype(float)
    n = len(s)
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    for i in range(left, n - right):
        window = s.iloc[i - left : i + right + 1]
        if s.iloc[i] == window.min() and (window == s.iloc[i]).sum() == 1:
            out.iloc[i + right] = s.iloc[i]
    return out


def crossed_over(series: pd.Series, level: float) -> bool:
    """True if the last closed bar crossed up through ``level``."""
    if level is None or len(series) < 2:
        return False
    return float(series.iloc[-2]) <= float(level) < float(series.iloc[-1])


def crossed_under(series: pd.Series, level: float) -> bool:
    if level is None or len(series) < 2:
        return False
    return float(series.iloc[-2]) >= float(level) > float(series.iloc[-1])


if __name__ == "__main__":  # self-check
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=10, freq="5min", tz="UTC"),
            "high": [10, 11, 15, 12, 11, 10, 9, 12, 13, 14],
            "low": [9, 10, 13, 11, 10, 9, 8, 11, 12, 13],
            "close": [9.5, 10.5, 14, 11.5, 10.5, 9.5, 8.5, 11.5, 12.5, 13.5],
            "volume": [1.0] * 10,
        }
    )
    ph = pivot_high(df["close"], 2, 2)
    assert ph.dropna().iloc[0] == 14.0  # the spike at index 2, confirmed at index 4
    assert crossed_over(pd.Series([9.0, 11.0]), 10.0)
    assert not crossed_over(pd.Series([11.0, 12.0]), 10.0)
    assert abs(ema(df["close"], 3).iloc[-1] - 12.36) < 0.2
    v = anchored_vwap(df)
    assert len(v) == 10 and v.notna().all()
    print("crypto.strategies.indicators self-check ok")
