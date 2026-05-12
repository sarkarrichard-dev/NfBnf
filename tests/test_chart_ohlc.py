from __future__ import annotations

import pandas as pd
import pytest

from trading_ai_engine.chart.ohlc_lightweight import ohlc_to_lightweight_chart

pytestmark = pytest.mark.unit


def test_ohlc_to_lightweight_chart_empty() -> None:
    df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = ohlc_to_lightweight_chart(df)
    assert out["count"] == 0
    assert out["bars"] == []


def test_ohlc_to_lightweight_chart_two_rows() -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.5],
            "close": [101.5, 102.2],
            "volume": [1000.0, 1100.0],
        }
    )
    out = ohlc_to_lightweight_chart(df, max_bars=50)
    assert out["count"] == 2
    assert len(out["bars"]) == 2
    b0 = out["bars"][0]
    assert {"time", "open", "high", "low", "close", "volume"} <= b0.keys()
    assert b0["open"] == 100.0
