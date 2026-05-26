from __future__ import annotations

import pandas as pd

from index_ai.breakout import detect_breakout


def test_break_res_when_close_crosses_range_high() -> None:
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(22)]
    base.append({"open": 101, "high": 102, "low": 100, "close": 101})
    base.append({"open": 105, "high": 106, "low": 104, "close": 105})
    candles = pd.DataFrame(base)
    result = detect_breakout(candles, lookback=20)
    assert result["ready"]
    assert result["break_res"] is True
    assert result["breakout_tag"] == "BREAK_RES"
