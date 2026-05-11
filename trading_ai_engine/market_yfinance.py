from __future__ import annotations

import pandas as pd
import yfinance as yf


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
    t = yf.Ticker(symbol)
    raw = t.history(period=period, interval=interval, auto_adjust=auto_adjust, prepost=False)
    return _normalize_ohlcv(raw)
