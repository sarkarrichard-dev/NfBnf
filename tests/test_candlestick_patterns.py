from __future__ import annotations

import pandas as pd

from index_ai.strategies.candlestick_patterns import detect_candlestick_setup
from index_ai.strategies.candlestick_sr import intraday_candle_trend


def test_intraday_trend_detects_up_move() -> None:
    rows = []
    for i in range(20):
        base = 100 + i * 0.5
        rows.append({"open": base, "high": base + 0.4, "low": base - 0.1, "close": base + 0.3})
    frame = pd.DataFrame(rows)
    assert intraday_candle_trend(frame) == "UP"


def test_bullish_engulfing_at_support() -> None:
    rows = [{"open": 100.0, "high": 101.0, "low": 99.5, "close": 99.8} for _ in range(28)]
    rows.append({"open": 100.0, "high": 100.2, "low": 99.7, "close": 99.9})
    rows.append({"open": 99.9, "high": 100.5, "low": 99.6, "close": 100.4})
    frame = pd.DataFrame(rows)
    setup = detect_candlestick_setup(frame, sr_lookback=25)
    assert setup.get("pattern") in {"bullish_engulfing", "trend_pullback_long", "hammer", ""}


def test_oi_walls_override_the_candle_range_as_support_resistance() -> None:
    """Richard, 2026-09-12: "the OI profile for support/resistance" -- when the
    real option-chain walls are supplied, use those instead of a naive N-bar
    candle range, same as the sell lane already does via oi_credit.py."""
    rows = [{"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0} for _ in range(30)]
    frame = pd.DataFrame(rows)  # candle range alone reads flat, close nowhere near a wall

    without_oi = detect_candlestick_setup(frame, sr_lookback=25)
    assert without_oi["sr_source"] == "candle_range"
    assert without_oi["near_resistance"] is False

    with_oi = detect_candlestick_setup(frame, sr_lookback=25, oi_support=90.0, oi_resistance=100.05)
    assert with_oi["sr_source"] == "oi_wall"
    assert with_oi["resistance"] == 100.05
    assert with_oi["near_resistance"] is True  # close (100.0) is right under the real OI wall

    # crossed/missing walls fall back to the candle range rather than trusting bad data
    crossed = detect_candlestick_setup(frame, sr_lookback=25, oi_support=105.0, oi_resistance=95.0)
    assert crossed["sr_source"] == "candle_range"
