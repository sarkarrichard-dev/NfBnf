"""Small indicator helpers not already in ``index_ai`` — EMA, anchored VWAP,
closing-basis swing pivots, and crossover tests. Pure pandas."""

from __future__ import annotations

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
    return out.fillna(hlc3) if out.isna().any() else out  # no volume → price is its own VWAP


def pivot_high(series: pd.Series, left: int, right: int) -> pd.Series:
    """Confirmed swing high on a closing basis. Non-NA at bar ``i`` carries the
    pivot value that formed ``right`` bars earlier (matches Pine ``ta.pivothigh``).

    Vectorised — no strict-uniqueness check, so an exact plateau flags every bar
    of the plateau; harmless for arming a level."""
    s = series.astype(float)
    w = left + right + 1
    win_max = s.rolling(w, min_periods=w).max().shift(-right)
    return s.where(s >= win_max).shift(right)


def pivot_low(series: pd.Series, left: int, right: int) -> pd.Series:
    s = series.astype(float)
    w = left + right + 1
    win_min = s.rolling(w, min_periods=w).min().shift(-right)
    return s.where(s <= win_min).shift(right)


def bollinger(series: pd.Series, length: int, dev: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(mid, upper, lower) — SMA ± dev·stdev (population)."""
    s = series.astype(float)
    mid = s.rolling(length, min_periods=length).mean()
    sd = s.rolling(length, min_periods=length).std(ddof=0)
    return mid, mid + dev * sd, mid - dev * sd


def cross_dir(a: pd.Series, b: pd.Series) -> int:
    """+1 if ``a`` closed the last bar crossing above ``b``, -1 if below, else 0."""
    if len(a) < 2 or len(b) < 2:
        return 0
    a0, a1 = float(a.iloc[-2]), float(a.iloc[-1])
    b0, b1 = float(b.iloc[-2]), float(b.iloc[-1])
    if a0 <= b0 and a1 > b1:
        return 1
    if a0 >= b0 and a1 < b1:
        return -1
    return 0


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
    mid, up, lo = bollinger(df["close"], 3, 2.0)
    assert (up.dropna() >= mid.dropna()).all() and (lo.dropna() <= mid.dropna()).all()
    assert cross_dir(pd.Series([1.0, 3.0]), pd.Series([2.0, 2.0])) == 1
    assert cross_dir(pd.Series([3.0, 1.0]), pd.Series([2.0, 2.0])) == -1
    assert cross_dir(pd.Series([1.0, 1.5]), pd.Series([2.0, 2.0])) == 0
    print("crypto.strategies.indicators self-check ok")
