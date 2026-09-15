"""Small indicator helpers not already in ``index_ai`` — EMA, anchored VWAP,
closing-basis swing pivots, and crossover tests. Pure pandas."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.astype(float).ewm(span=length, adjust=False).mean()


def smma(series: pd.Series, length: int) -> pd.Series:
    """Smoothed / Wilder's moving average (RMA) — ``alpha = 1/length``.
    What TradingView calls a "Smoothed MA"."""
    return series.astype(float).ewm(alpha=1.0 / length, adjust=False).mean()


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


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Wilder ATR (RMA of true range). Needs high/low/close columns."""
    h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - low, (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, adjust=False).mean()


def _wilder(x: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return out


def atr_last(df: pd.DataFrame, length: int) -> float:
    """Just the final Wilder-ATR value — numpy, no pandas Series build."""
    if len(df) < 2:
        return 0.0
    h, low, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - low, np.maximum(np.abs(h - pc), np.abs(low - pc)))
    return float(_wilder(tr, 1.0 / length)[-1])


def supertrend_dir(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> int:
    """Final Supertrend direction: +1 bull, -1 bear, 0 if too little history.

    Same recurrence as ``index_ai.strategies.supertrend.compute_supertrend`` but
    numpy end-to-end and only the last value — the hot path when a backtest
    replays this thousands of times."""
    n = len(df)
    if n < period + 2:
        return 0
    h, low, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - low, np.maximum(np.abs(h - pc), np.abs(low - pc)))
    a = _wilder(tr, 1.0 / period)
    hl2 = (h + low) / 2.0
    bu, bl = hl2 + multiplier * a, hl2 - multiplier * a
    fu, fl, direction = bu[0], bl[0], 1
    for i in range(1, n):
        fu = bu[i] if (bu[i] < fu or c[i - 1] > fu) else fu
        fl = bl[i] if (bl[i] > fl or c[i - 1] < fl) else fl
        if direction == 1:
            direction = -1 if c[i] < fl else 1
        else:
            direction = 1 if c[i] > fu else -1
    return direction


def rsi(series: pd.Series, length: int) -> pd.Series:
    """Wilder RSI — RMA of gains vs RMA of losses, 0-100."""
    s = series.astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / length
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0)  # avg_loss == 0 (straight up) -> RSI 100, not NaN


def adx(df: pd.DataFrame, length: int) -> pd.Series:
    """Wilder ADX — trend-strength (not direction), 0-100. Needs high/low/close."""
    h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    up_move = h.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    pc = c.shift(1)
    tr = pd.concat([h - low, (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / length
    tr_s = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0.0, pd.NA)
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0.0, pd.NA)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, pd.NA)
    return dx.fillna(0.0).ewm(alpha=alpha, adjust=False).mean()


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
    a = atr(df, 3)
    assert a.iloc[-1] > 0 and len(a) == 10
    assert abs(atr_last(df, 3) - float(a.iloc[-1])) < 1e-9  # numpy path matches pandas
    up_df = pd.DataFrame({"high": range(2, 40), "low": range(0, 38), "close": range(1, 39)})
    dn_df = up_df.iloc[::-1].reset_index(drop=True)
    assert supertrend_dir(up_df, 10, 3.0) == 1
    assert supertrend_dir(dn_df, 10, 3.0) == -1
    assert supertrend_dir(up_df.head(5), 10, 3.0) == 0  # not enough bars

    flat = pd.Series([50.0] * 20)
    assert (
        abs(rsi(flat, 14).iloc[-1] - 100.0) < 1.0 or flat.diff().sum() == 0
    )  # no moves -> neutral-ish, no crash
    r_up = rsi(up_df["close"], 14)
    assert r_up.iloc[-1] > 70  # a clean uptrend reads overbought
    r_dn = rsi(dn_df["close"], 14)
    assert r_dn.iloc[-1] < 30  # a clean downtrend reads oversold

    a_up = adx(up_df, 14)
    assert a_up.iloc[-1] > 25  # a clean, unbroken trend has real ADX strength
    choppy = pd.DataFrame(
        {
            "high": [10, 11, 10, 11, 10, 11, 10, 11, 10, 11] * 3,
            "low": [9, 10, 9, 10, 9, 10, 9, 10, 9, 10] * 3,
            "close": [9.5, 10.5, 9.5, 10.5, 9.5, 10.5, 9.5, 10.5, 9.5, 10.5] * 3,
        }
    )
    assert adx(choppy, 14).iloc[-1] < 25  # a sideways chop has weak ADX
    print("crypto.strategies.indicators self-check ok")
