from __future__ import annotations

import pandas as pd

from index_ai.strategies.supertrend import compute_supertrend, supertrend_snapshot


def _trending_up(n: int = 40) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        price += 1.5
        rows.append(
            {
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
            }
        )
    return pd.DataFrame(rows)


def test_supertrend_bullish_on_uptrend() -> None:
    frame = compute_supertrend(_trending_up(), period=10, multiplier=3.0)
    snap = supertrend_snapshot(_trending_up(), period=10, multiplier=3.0)
    assert snap["ready"]
    assert int(frame.iloc[-1]["supertrend_direction"]) == 1
    assert snap["direction"] == 1
    assert snap["stop"] > 0
