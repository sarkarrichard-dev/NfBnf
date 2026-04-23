from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_ohlc_csv(path: str | Path, *, date_col: str = "date") -> pd.DataFrame:
    """Load OHLC-style data from CSV; expects a parseable date column."""
    df = pd.read_csv(path, parse_dates=[date_col])
    return df.sort_values(date_col).reset_index(drop=True)


def empty_ohlc_frame() -> pd.DataFrame:
    """Empty OHLC-shaped frame for merging API responses into one schema."""
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
