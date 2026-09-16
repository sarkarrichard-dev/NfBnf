from __future__ import annotations

import pandas as pd

from index_ai.strategies.buy_strategy import evaluate_buy_signal
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.strategy_params import reload_strategy_params


def _regime(day_bias: str) -> CprRegime:
    return CprRegime(
        pivot=100.0,
        bc=98.0,
        tc=102.0,
        width=4.0,
        width_pct=4.0,
        width_class="NARROW",
        cpr_type="BULLISH",
        virgin_cpr=False,
        price_position="above_cpr",
        day_bias=day_bias,
        note="",
    )


def _breakout_candles() -> pd.DataFrame:
    """A flat range around 100, then two bars confirming a real breakout above
    it — matches the default entry_confirmation_bars=2, so this should read
    as a genuine ``breakout_resistance`` pattern regardless of CPR regime."""
    rows = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0} for _ in range(25)]
    rows.append({"open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0})
    rows.append({"open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0})
    return pd.DataFrame(rows)


def test_breakout_buy_vetoed_when_cpr_reads_sideways(monkeypatch) -> None:
    """2026-09-16: the day's worst loss was a breakout_resistance buy while
    CPR itself read SIDEWAYS — the market saying it's directionless while the
    lane bought a breakout anyway. Must be blocked now."""
    monkeypatch.setenv("REQUIRE_SUPERTREND_ALIGN", "false")
    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 103, "low": 97, "close": 100},
            {"open": 100, "high": 103, "low": 97, "close": 101},
        ]
    )
    frame = _breakout_candles()

    sideways = evaluate_buy_signal(frame, previous, _regime("SIDEWAYS"))
    assert sideways.action == "NO_TRADE"
    assert sideways.entry_quality == "cpr_sideways_veto"

    # same candles, a trending regime — the pattern is real, so it must fire
    trending = evaluate_buy_signal(frame, previous, _regime("TRENDING_BULL"))
    assert trending.action == "BUY_CALL"
    assert trending.entry_quality == "breakout_resistance"


def test_reversal_patterns_are_not_gated_by_sideways_cpr(monkeypatch) -> None:
    """The SIDEWAYS veto is scoped to the two breakout-continuation patterns
    only — a reversal pattern (bullish engulfing at support) makes a
    different bet and must still fire on a SIDEWAYS day."""
    monkeypatch.setenv("REQUIRE_SUPERTREND_ALIGN", "false")
    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 103, "low": 97, "close": 100},
            {"open": 100, "high": 103, "low": 97, "close": 101},
        ]
    )
    # index-scale prices — the support tolerance is 0.15% of price, too tight
    # for a real reversal candle's body at toy ~100-level prices
    rows = [{"open": 23920.0, "high": 23930.0, "low": 23900.0, "close": 23920.0} for _ in range(27)]
    rows.append({"open": 23920.0, "high": 23925.0, "low": 23900.0, "close": 23905.0})  # bearish
    rows.append({"open": 23900.0, "high": 23935.0, "low": 23895.0, "close": 23930.0})  # engulfs it
    frame = pd.DataFrame(rows)

    result = evaluate_buy_signal(frame, previous, _regime("SIDEWAYS"))
    assert result.action == "BUY_CALL"
    assert result.entry_quality == "bullish_engulfing"
