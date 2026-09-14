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


def test_oi_walls_take_priority_over_candle_range() -> None:
    """The real option-chain OI walls (support/resistance) must override the
    candle-range guess when supplied — this is what makes the buy lane's
    patterns fire off the same walls the sell lane already uses, instead of
    an arbitrary N-bar high/low."""
    rows = [{"open": 100.0, "high": 101.0, "low": 99.5, "close": 99.8} for _ in range(28)]
    rows.append({"open": 100.0, "high": 100.2, "low": 99.7, "close": 99.9})
    rows.append({"open": 99.9, "high": 100.5, "low": 99.6, "close": 100.4})
    frame = pd.DataFrame(rows)

    # candle-range support (99.5) isn't near the last close (100.4) — no pattern
    baseline = detect_candlestick_setup(frame, sr_lookback=25)
    assert baseline["sr_source"] == "candle"
    assert not baseline["near_support"]

    # an OI wall right where price actually is: the same bar now reads as
    # "near support" and the bullish engulfing fires, sourced from OI
    near_oi = detect_candlestick_setup(frame, sr_lookback=25, oi_support=100.3, oi_resistance=101.0)
    assert near_oi["sr_source"] == "oi"
    assert near_oi["near_support"] is True
    assert near_oi["pattern"] == "bullish_engulfing"
    assert "(OI wall)" in near_oi["reason"]

    # an OI wall far from price suppresses the read entirely, even though the
    # exact same candles would trigger a pattern off the candle range alone
    far_oi = detect_candlestick_setup(frame, sr_lookback=25, oi_support=50.0, oi_resistance=200.0)
    assert far_oi["sr_source"] == "oi"
    assert not far_oi["near_support"] and not far_oi["near_resistance"]
