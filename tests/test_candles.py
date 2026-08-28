from __future__ import annotations

import pandas as pd

from index_ai.candles import prepare_intraday_signal_frames
from index_ai.strategies.strategy import cpr_ema_signal


def test_prepare_intraday_uses_rolling_tail_when_today_is_thin() -> None:
    rows = []
    # Prior session: 30 bars
    for i in range(30):
        rows.append(
            {
                "datetime": pd.Timestamp("2026-05-22 09:15:00") + pd.Timedelta(minutes=5 * i),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100 + i * 0.1,
            }
        )
    # Today: only 9 bars (early session)
    for i in range(9):
        rows.append(
            {
                "datetime": pd.Timestamp("2026-05-25 09:15:00") + pd.Timedelta(minutes=5 * i),
                "open": 120,
                "high": 121,
                "low": 119,
                "close": 120 + i,
            }
        )
    candles = pd.DataFrame(rows)
    ema_frame, previous = prepare_intraday_signal_frames(candles)
    assert len(ema_frame) == 21
    assert len(previous) == 30
    signal = cpr_ema_signal(ema_frame, previous)
    assert signal.action in ("BUY_CALL", "BUY_PUT", "NO_TRADE")
