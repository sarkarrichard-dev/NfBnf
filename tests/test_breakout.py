from __future__ import annotations

import pandas as pd

from index_ai.strategies.breakout import detect_breakout


def test_break_res_when_close_crosses_range_high() -> None:
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(22)]
    base.append({"open": 101, "high": 102, "low": 100, "close": 101})
    base.append({"open": 105, "high": 106, "low": 104, "close": 105})
    candles = pd.DataFrame(base)
    result = detect_breakout(candles, lookback=20)
    assert result["ready"]
    assert result["break_res"] is True
    assert result["breakout_tag"] == "BREAK_RES"


def test_confirm_bars_rejects_a_single_bar_poke_through() -> None:
    """2026-09-16: a one-bar close above the range proves nothing — it's the
    exact shape of a fakeout that reverses straight into the hard stop.
    confirm_bars=2 must reject a single-bar break the old default accepted."""
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(22)]
    base.append({"open": 105, "high": 106, "low": 104, "close": 105})  # pokes through once
    base.append({"open": 100, "high": 101, "low": 99, "close": 100})  # snaps back — not confirmed
    candles = pd.DataFrame(base)
    result = detect_breakout(candles, lookback=20, confirm_bars=2)
    assert result["break_res"] is False


def test_confirm_bars_accepts_two_real_confirmed_closes() -> None:
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(22)]
    base.append({"open": 105, "high": 106, "low": 104, "close": 105})
    base.append({"open": 105, "high": 107, "low": 104, "close": 106})  # holds above the level
    candles = pd.DataFrame(base)
    result = detect_breakout(candles, lookback=20, confirm_bars=2)
    assert result["break_res"] is True
    assert result["breakout_tag"] == "BREAK_RES"


def test_confirm_bars_one_matches_the_old_single_bar_behaviour() -> None:
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(22)]
    base.append({"open": 101, "high": 102, "low": 100, "close": 101})
    base.append({"open": 105, "high": 106, "low": 104, "close": 105})
    candles = pd.DataFrame(base)
    default_result = detect_breakout(candles, lookback=20)
    explicit_result = detect_breakout(candles, lookback=20, confirm_bars=1)
    assert default_result == explicit_result
    assert explicit_result["break_res"] is True
