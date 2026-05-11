"""
Classic candlestick pattern flags (0/1) for supervised training and live gating.

Heuristics are research-style (not exchange-certified pattern definitions).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Float columns appended to OHLCV before supervised labeling.
PATTERN_FEATURE_COLUMNS: list[str] = [
    "pat_doji",
    "pat_bull_engulf",
    "pat_bear_engulf",
    "pat_hammer",
    "pat_shooting_star",
    "pat_marubozu_bull",
    "pat_marubozu_bear",
    "pat_piercing",
    "pat_dark_cloud",
    "pat_morning_star",
    "pat_evening_star",
    "pat_three_white_soldiers",
    "pat_three_black_crows",
]

# +1 bullish bias, -1 bearish, 0 neutral / context-dependent treated as weak directional hints.
PATTERN_DIRECTION: dict[str, int] = {
    "pat_doji": 0,
    "pat_bull_engulf": 1,
    "pat_bear_engulf": -1,
    "pat_hammer": 1,
    "pat_shooting_star": -1,
    "pat_marubozu_bull": 1,
    "pat_marubozu_bear": -1,
    "pat_piercing": 1,
    "pat_dark_cloud": -1,
    "pat_morning_star": 1,
    "pat_evening_star": -1,
    "pat_three_white_soldiers": 1,
    "pat_three_black_crows": -1,
}


def pattern_direction(name: str) -> int:
    return int(PATTERN_DIRECTION.get(name, 0))


def _ensure_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            if "close" in out.columns:
                out[col] = out["close"]
            else:
                out[col] = 0.0
        else:
            out[col] = out[col].astype(float)
    return out


def add_pattern_features(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Add ``pat_*`` float columns (0.0 / 1.0) in-place on a copy of ``ohlc``."""
    df = _ensure_ohlc(ohlc)
    o = df["open"]
    h = df["high"]
    l_ = df["low"]
    c = df["close"]
    rng = (h - l_).replace(0, pd.NA)
    body = (c - o).abs()
    body_pct = body / rng
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l_
    bull = c > o
    bear = c < o

    # Doji: tiny body vs range
    df["pat_doji"] = ((body_pct < 0.12) & (rng > 0)).astype(float).fillna(0.0)

    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_bear = prev_c < prev_o
    prev_bull = prev_c > prev_o
    df["pat_bull_engulf"] = (
        prev_bear & bull & (o <= prev_c) & (c >= prev_o) & (c > o) & (body > 0)
    ).astype(float)
    df["pat_bear_engulf"] = (
        prev_bull & bear & (o >= prev_c) & (c <= prev_o) & (c < o) & (body > 0)
    ).astype(float)

    df["pat_hammer"] = (
        (lower >= 2.0 * body) & (upper <= body * 1.1) & (body_pct < 0.45) & (rng > 0)
    ).astype(float)
    df["pat_shooting_star"] = (
        (upper >= 2.0 * body) & (lower <= body * 1.1) & (body_pct < 0.45) & (rng > 0)
    ).astype(float)

    df["pat_marubozu_bull"] = (bull & (body_pct > 0.88) & (rng > 0)).astype(float)
    df["pat_marubozu_bear"] = (bear & (body_pct > 0.88) & (rng > 0)).astype(float)

    mid_prev = (prev_o + prev_c) / 2.0
    gap_down = o < l_.shift(1)
    gap_up = o > h.shift(1)
    df["pat_piercing"] = (
        prev_bear & bull & gap_down & (c > mid_prev) & (c < prev_o) & (c > o)
    ).astype(float)
    df["pat_dark_cloud"] = (
        prev_bull & bear & gap_up & (c < mid_prev) & (c > prev_c) & (c < o)
    ).astype(float)

    b2, b1 = c.shift(2), c.shift(1)
    o2, o1 = o.shift(2), o.shift(1)
    long_red = (b2 < o2) & ((o2 - b2) / (h.shift(2) - l_.shift(2)).replace(0, pd.NA) > 0.55)
    small_mid = (b1 - o1).abs() / (h.shift(1) - l_.shift(1)).replace(0, pd.NA) < 0.35
    long_green = bull & ((c - o) / rng > 0.55)
    df["pat_morning_star"] = (long_red.fillna(False) & small_mid.fillna(False) & long_green.fillna(False)).astype(
        float
    )

    long_green_prev = (b2 > o2) & ((b2 - o2) / (h.shift(2) - l_.shift(2)).replace(0, pd.NA) > 0.55)
    long_red_now = bear & ((o - c) / rng > 0.55)
    df["pat_evening_star"] = (
        long_green_prev.fillna(False) & small_mid.fillna(False) & long_red_now.fillna(False)
    ).astype(float)

    bull3 = bull & bull.shift(1) & bull.shift(2)
    stair_up = (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    df["pat_three_white_soldiers"] = (bull3 & stair_up).astype(float)
    bear3 = bear & bear.shift(1) & bear.shift(2)
    stair_dn = (c < c.shift(1)) & (c.shift(1) < c.shift(2))
    df["pat_three_black_crows"] = (bear3 & stair_dn).astype(float)

    for col in PATTERN_FEATURE_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype(float).clip(0.0, 1.0).fillna(0.0)
    return df


def last_bar_pattern_dict(ohlc: pd.DataFrame) -> dict[str, float]:
    """Pattern values for the most recent row (for persistence on paper orders)."""
    if ohlc is None or ohlc.empty:
        return {k: 0.0 for k in PATTERN_FEATURE_COLUMNS}
    df = add_pattern_features(ohlc)
    last = df.iloc[-1]
    return {k: float(last.get(k) or 0.0) for k in PATTERN_FEATURE_COLUMNS}


def snapshot_patterns_by_tf(multi_tf: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for tf, frame in multi_tf.items():
        if frame is None or frame.empty:
            continue
        d = last_bar_pattern_dict(frame)
        if any(v > 0.5 for v in d.values()):
            out[tf] = {k: v for k, v in d.items() if v > 0.5}
    return out


def calibrate_patterns(
    frame: pd.DataFrame,
    *,
    min_samples: int = 40,
) -> dict[str, Any]:
    """
    Empirical win-rate for each pattern × interval bucket.
    Win = label matches ``PATTERN_DIRECTION`` (+1 needs label==1, -1 needs label==-1).
    """
    if frame.empty or "label" not in frame.columns:
        return {}
    intervals = sorted(frame["interval"].dropna().unique()) if "interval" in frame.columns else ["1d"]
    out: dict[str, dict[str, Any]] = {}
    for iv in [str(x) for x in intervals]:
        sub = frame[frame["interval"] == iv] if "interval" in frame.columns else frame
        out[iv] = {}
        for col in PATTERN_FEATURE_COLUMNS:
            if col not in sub.columns:
                continue
            d = pattern_direction(col)
            if d == 0:
                continue
            hits = sub[sub[col] > 0.5]
            n = int(len(hits))
            if n < min_samples:
                out[iv][col] = {"n": n, "win_rate": None, "direction": d}
                continue
            wins = int((hits["label"] == d).sum())
            out[iv][col] = {
                "n": n,
                "win_rate": round(wins / max(n, 1), 4),
                "direction": d,
            }
    return out

