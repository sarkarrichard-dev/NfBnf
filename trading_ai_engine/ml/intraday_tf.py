"""Resample OHLCV for multi-timeframe training (5m → 3h)."""

from __future__ import annotations

import pandas as pd

# Pandas offset aliases: expand from a 5m Yahoo frame up to 3h bars.
INTRADAY_TF_RULES: tuple[tuple[str, str], ...] = (
    ("15min", "15m"),
    ("30min", "30m"),
    ("60min", "60m"),
    ("90min", "90m"),
    ("2h", "120m"),
    ("3h", "180m"),
)


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df.iloc[0:0].copy()
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"])
    d = d.set_index("date").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c not in d.columns:
            d[c] = d["close"] if c != "volume" else 0.0
        d[c] = d[c].astype(float)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = d.resample(rule).agg(agg).dropna(subset=["close"])
    return out.reset_index()


def expand_base_5m_to_timeframes(df_5m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """From a 5m Yahoo frame, derive coarser bars up to 3h."""
    out: dict[str, pd.DataFrame] = {}
    if df_5m is None or df_5m.empty:
        return out
    out["5m"] = df_5m.copy()
    base = df_5m
    for rule, label in INTRADAY_TF_RULES:
        r = resample_ohlc(base, rule)
        if not r.empty:
            out[label] = r
    return out
