from __future__ import annotations

import contextlib
import os
import sys
from typing import Iterator

import pandas as pd
import yfinance as yf


@contextlib.contextmanager
def _yfinance_quiet_stderr() -> Iterator[None]:
    """yfinance prints HTTP 404 / delisting hints to stderr; keep workstation logs readable."""
    devnull = open(os.devnull, "w", encoding="utf-8")
    old = sys.stderr
    try:
        sys.stderr = devnull
        yield
    finally:
        sys.stderr = old
        devnull.close()


def _normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Map yfinance columns to a stable schema for later swapping (e.g. Dhan API)."""
    if raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df = df.reset_index()
    # yfinance index name is often "Date" after reset_index
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    out = df.rename(columns={date_col: "date"})
    cols = ["date", "open", "high", "low", "close", "volume"]
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[cols].sort_values("date").reset_index(drop=True)
    dts = pd.to_datetime(out["date"], errors="coerce")
    if dts.dt.tz is not None:
        dts = dts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    out["date"] = dts
    return out


def history_range(
    symbol: str,
    *,
    start: str,
    end: str,
    interval: str = "1d",
    auto_adjust: bool = False,
) -> pd.DataFrame:
    """OHLCV between ``start`` and ``end`` (YYYY-MM-DD). Used for post-mortem forward returns."""
    with _yfinance_quiet_stderr():
        t = yf.Ticker(symbol)
        raw = t.history(start=start, end=end, interval=interval, auto_adjust=auto_adjust, prepost=False)
    return _normalize_ohlcv(raw)


def history(
    symbol: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    auto_adjust: bool = False,
) -> pd.DataFrame:
    """
    Daily (or other) OHLCV from Yahoo Finance via yfinance.

    Indian listings typically use Yahoo suffixes, e.g. ``RELIANCE.NS`` (NSE) or
    ``RELIANCE.BO`` (BSE). This is a stopgap until Dhan (or another broker) feeds
    replace the source.
    """
    with _yfinance_quiet_stderr():
        t = yf.Ticker(symbol)
        raw = t.history(period=period, interval=interval, auto_adjust=auto_adjust, prepost=False)
    return _normalize_ohlcv(raw)


def last_daily_close(symbol: str, *, lookback_days: int = 15) -> float | None:
    """Most recent daily close from Yahoo (best-effort for paper marks)."""
    period = f"{max(5, min(lookback_days, 60))}d"
    df = history(symbol, period=period, interval="1d", auto_adjust=False)
    if df.empty or "close" not in df.columns:
        return None
    last = df["close"].iloc[-1]
    if pd.isna(last):
        return None
    return float(last)
