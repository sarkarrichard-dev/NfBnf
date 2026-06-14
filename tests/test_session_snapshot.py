from __future__ import annotations

import pandas as pd

from index_ai.session_snapshot import spot_session_metrics


def test_spot_session_metrics_volume_and_cpr() -> None:
    today = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0],
            "volume": [1000, 2000, 3000],
            "ema_fast": [101.0, 101.5, 102.0],
            "ema_slow": [100.0, 100.5, 101.0],
        }
    )
    snap = spot_session_metrics(
        today,
        signal={"price": 102.0, "pivot": 100, "bc": 99, "tc": 101, "ema_fast": 102, "ema_slow": 101},
        regime={"day_bias": "TRENDING_BULL", "width_pct": 0.4, "width_class": "NORMAL", "virgin_cpr": True},
    )
    assert snap["session_volume"] == 6000
    assert snap["ema_bias"] == "bullish"
    assert snap["cpr_position"] == "above_tc"
    assert snap["bars"] == 3
