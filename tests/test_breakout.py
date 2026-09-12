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


def test_confirm_bars_rejects_a_single_bar_fake_breakout() -> None:
    """Richard, 2026-09-12: don't fire a breakout on a single-bar poke that
    hasn't held -- a live scanner can't know the next bar in advance, so
    requiring confirm_bars closes beyond the level (instead of just the
    latest one) is the live-safe stand-in for fake-breakout detection."""
    base = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(25)]
    base.append({"open": 102, "high": 104, "low": 101.5, "close": 103})  # fresh, unconfirmed poke
    candles = pd.DataFrame(base)

    assert detect_breakout(candles, lookback=20, confirm_bars=1)["break_res"] is True
    assert detect_breakout(candles, lookback=20, confirm_bars=2)["break_res"] is False
