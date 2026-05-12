from __future__ import annotations

import pandas as pd
import pytest

from trading_ai_engine.brain.council import infer_council
from trading_ai_engine.brain.ml_core import infer as ml_infer
from trading_ai_engine.ml.features import build_features

pytestmark = [pytest.mark.unit]


def test_council_heuristic_returns_agents() -> None:
    rows = [
        {"date": pd.Timestamp("2024-01-02"), "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1e6},
        {"date": pd.Timestamp("2024-01-03"), "open": 101, "high": 105, "low": 100, "close": 104, "volume": 1.1e6},
        {"date": pd.Timestamp("2024-01-04"), "open": 104, "high": 106, "low": 103, "close": 105, "volume": 1e6},
    ]
    ohlc = pd.DataFrame(rows)
    metrics, tags = build_features(ohlc)
    ml = ml_infer(metrics, tags, ohlc)
    ai, report = infer_council("TEST.NS", metrics, ml, 0.0, use_llm=False)
    assert report.get("mode") == "heuristic"
    assert len(report.get("agents") or []) == 3
    assert ai.stance in ("bullish", "bearish", "neutral")
    assert ai.version.startswith("ai_council")
