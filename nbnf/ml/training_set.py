from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from nbnf.ml.features import _rsi


@dataclass(frozen=True)
class LabelConfig:
    horizon_bars: int = 5
    up_threshold: float = 0.005
    down_threshold: float = -0.005


FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ret_5",
    "sma20_gap",
    "sma50_gap",
    "rsi14",
    "vol_z",
]


def make_supervised_frame(ohlc: pd.DataFrame, config: LabelConfig | None = None) -> pd.DataFrame:
    """
    Build a simple supervised learning table from normalized OHLCV.

    Each row describes information available at that bar. The label is the future close return
    after ``horizon_bars``. This is the first honest ML-ready table: features and labels are
    explicit, and no future data is used in the feature columns.
    """
    cfg = config or LabelConfig()
    df = ohlc.dropna(subset=["close"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["date", *FEATURE_COLUMNS, "future_return", "label"])

    close = df["close"].astype(float)
    volume = df["volume"].astype(float).fillna(0.0)

    df["ret_1"] = close.pct_change(1)
    df["ret_3"] = close.pct_change(3)
    df["ret_5"] = close.pct_change(5)
    sma20 = close.rolling(20, min_periods=10).mean()
    sma50 = close.rolling(50, min_periods=25).mean()
    df["sma20_gap"] = close / sma20 - 1.0
    df["sma50_gap"] = close / sma50 - 1.0
    df["rsi14"] = _rsi(close, 14)
    vol_mean = volume.rolling(20, min_periods=10).mean()
    vol_std = volume.rolling(20, min_periods=10).std()
    df["vol_z"] = (volume - vol_mean) / vol_std.replace(0, pd.NA)

    future_close = close.shift(-cfg.horizon_bars)
    df["future_return"] = future_close / close - 1.0
    df["label"] = 0
    df.loc[df["future_return"] >= cfg.up_threshold, "label"] = 1
    df.loc[df["future_return"] <= cfg.down_threshold, "label"] = -1

    keep = ["date", *FEATURE_COLUMNS, "future_return", "label"]
    out = df[keep].dropna().reset_index(drop=True)
    return out


def describe_training_frame(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "labels": {}, "date_min": None, "date_max": None}
    label_counts = frame["label"].value_counts().sort_index()
    return {
        "rows": int(len(frame)),
        "features": list(FEATURE_COLUMNS),
        "labels": {str(int(k)): int(v) for k, v in label_counts.items()},
        "date_min": str(frame["date"].min()),
        "date_max": str(frame["date"].max()),
    }
