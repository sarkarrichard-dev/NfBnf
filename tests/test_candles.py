from __future__ import annotations

import pandas as pd

from index_ai.candles import prepare_intraday_signal_frames, resample_ohlcv
from index_ai.strategies.strategy import cpr_ema_signal


def test_resample_ohlcv_aggregates_and_respects_sessions() -> None:
    n = 30
    one = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-22 09:15", periods=n, freq="1min"),
            "open": range(n),
            "high": [x + 2 for x in range(n)],
            "low": [x - 2 for x in range(n)],
            "close": [x + 1 for x in range(n)],
            "volume": [100.0] * n,
        }
    )
    five = resample_ohlcv(one, "5min")
    assert len(five) == 6
    assert list(five.columns) == list(one.columns)
    assert five.iloc[0]["open"] == 0 and five.iloc[0]["high"] == 6 and five.iloc[0]["close"] == 5
    assert five.iloc[0]["volume"] == 500.0
    # two sessions -> bars never merge across the overnight gap
    two_days = pd.concat(
        [one, one.assign(datetime=pd.date_range("2026-05-23 09:15", periods=n, freq="1min"))],
        ignore_index=True,
    )
    assert len(resample_ohlcv(two_days, "15min")) == 4  # 2 per session
    # already coarse -> unchanged
    assert len(resample_ohlcv(five, "5min")) == 6


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
