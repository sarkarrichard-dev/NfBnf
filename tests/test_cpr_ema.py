from __future__ import annotations

import pandas as pd
import pytest

from trading_ai_engine.ml.cpr_ema import add_cpr_ema_columns, cpr_structure_bias, last_row_cpr_ema_metrics

pytestmark = [pytest.mark.unit]


def test_cpr_prev_day_shift() -> None:
    rows = []
    for d, h, l_, c in [
        ("2024-06-03", 110.0, 100.0, 105.0),
        ("2024-06-03", 112.0, 101.0, 108.0),
        ("2024-06-04", 109.0, 102.0, 106.0),
    ]:
        rows.append({"date": d, "open": c, "high": h, "low": l_, "close": c, "volume": 1e6})
    df = pd.DataFrame(rows)
    out = add_cpr_ema_columns(df, drop_intermediate=True)
    june4 = out[out["date"].astype(str).str.startswith("2024-06-04")]
    assert not june4.empty
    p_prev = (112.0 + 100.0 + 108.0) / 3.0  # min low 100 across Jun 3 bars
    assert abs(float(june4.iloc[0]["cpr_pct_from_p"]) - (106.0 - p_prev) / 106.0) < 1e-4


def test_cpr_bias_above_pivot() -> None:
    b = cpr_structure_bias(close=102.0, p=100.0, bc=99.0, tc=101.0)
    assert b > 0


def test_last_row_metrics_keys() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=80, freq="D"),
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 100.0 + pd.Series(range(80)) * 0.02,
            "volume": 1e6,
        }
    )
    m = last_row_cpr_ema_metrics(df)
    assert "cpr_structure_bias" in m
    assert "ema_stack_bias" in m
    assert "ema9_gap" in m
