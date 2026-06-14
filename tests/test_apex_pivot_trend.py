from __future__ import annotations

import pandas as pd

from index_ai.apex_pivot_trend import apex_pivot_trend_signal
from index_ai.pivot_points import classic_pivot_levels


def _previous() -> pd.DataFrame:
    return pd.DataFrame(
        {"high": [100, 110], "low": [90, 95], "close": [105, 108]}
    )


def test_pivot_r1_s1_math() -> None:
    pp, r1, s1, _ = classic_pivot_levels(_previous())
    assert pp > 0
    assert r1 > pp
    assert s1 < pp


def test_no_trade_inside_r1_s1() -> None:
    pp, r1, s1, _ = classic_pivot_levels(_previous())
    mid = (r1 + s1) / 2
    today = pd.DataFrame(
        {
            "open": [mid] * 30,
            "high": [mid + 1] * 30,
            "low": [mid - 1] * 30,
            "close": [mid] * 30,
        }
    )
    sig = apex_pivot_trend_signal(today, _previous())
    assert sig.action == "NO_TRADE"
    assert "R1" in sig.reason or "range" in sig.reason.lower()


def test_sell_put_above_r1_bull_st() -> None:
    pp, r1, s1, _ = classic_pivot_levels(_previous())
    price = r1 + 50
    today = pd.DataFrame(
        {
            "open": [price - i for i in range(30)],
            "high": [price + 2] * 30,
            "low": [price - 5] * 30,
            "close": [price] * 30,
        }
    )
    sig = apex_pivot_trend_signal(today, _previous())
    assert sig.action in {"SELL_ATM_PUT", "NO_TRADE"}
