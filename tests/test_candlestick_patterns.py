from __future__ import annotations

import pandas as pd

from trading_ai_engine.ml.candlestick_patterns import (
    PATTERN_FEATURE_COLUMNS,
    calibrate_patterns,
    last_bar_pattern_dict,
)
from trading_ai_engine.ml.cpr_ema import CPR_EMA_FEATURE_COLUMNS
from trading_ai_engine.ml.training_set import LabelConfig, make_supervised_frame


def test_hammer_detected_last_bar() -> None:
    rows = []
    for i in range(25):
        o, h, l_, c = 100.0, 101.0, 99.5, 100.2
        if i == 24:
            o, h, l_, c = 100.0, 100.12, 97.0, 100.1
        rows.append({"date": f"2024-01-{i+1:02d}", "open": o, "high": h, "low": l_, "close": c, "volume": 1e6})
    df = pd.DataFrame(rows)
    d = last_bar_pattern_dict(df)
    assert d.get("pat_hammer", 0) >= 0.99


def test_supervised_frame_has_pattern_columns() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=120, freq="D"),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.5 + pd.Series(range(120)) * 0.01,
            "volume": 1e6,
        }
    )
    out = make_supervised_frame(df, LabelConfig(horizon_bars=3), interval="1d")
    assert "interval" in out.columns
    for c in PATTERN_FEATURE_COLUMNS:
        assert c in out.columns
    for c in CPR_EMA_FEATURE_COLUMNS:
        assert c in out.columns


def test_calibrate_patterns_smoke() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=200, freq="D"),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0 + pd.Series(range(200)) * 0.05,
            "volume": 1e6,
        }
    )
    fr = make_supervised_frame(df, LabelConfig(horizon_bars=2), interval="1d")
    cal = calibrate_patterns(fr, min_samples=5)
    assert isinstance(cal, dict)
