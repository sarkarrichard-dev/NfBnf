from __future__ import annotations

import pandas as pd

from index_ai.strategies.cpr_regime import CprRegime, analyze_cpr_regime
from index_ai.strategies.ema_cross import analyze_ema_cross
from index_ai.strategies.strategy_mode import pick_auto_credit
from index_ai.strategies.strategy_params import reload_strategy_params
from index_ai.strategies.strategy_router import route_intraday_signal


def _sideways_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    today = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    return today, previous


def test_auto_sideways_no_range_sell() -> None:
    # Directional-only credit policy: a sideways CPR is a no-trade, never an iron condor.
    today, previous = _sideways_frames()
    frame = today.copy()
    cross = analyze_ema_cross(frame, fast=8, slow=20)
    regime = analyze_cpr_regime(frame, previous)
    action, reason, mode = pick_auto_credit(regime, cross, ema_fast=8, ema_slow=20)
    assert regime.day_bias == "SIDEWAYS"
    assert action is None
    assert mode == "wait"
    assert "directional" in reason.lower()


def test_auto_blocks_bear_call_when_ema_bull_vs_bear_cpr() -> None:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 95, "close": 108},
            {"open": 108, "high": 112, "low": 104, "close": 110},
        ]
    )
    n = 25
    today = pd.DataFrame(
        {
            "open": [90.0] * n,
            "high": [91.0] * n,
            "low": [89.0] * n,
            "close": [90.0] * n,
            "ema_fast": [92.0] * n,
            "ema_slow": [91.0] * n,
        }
    )
    cross = analyze_ema_cross(today, fast=8, slow=20)
    regime = analyze_cpr_regime(today, previous)
    action, reason, mode = pick_auto_credit(regime, cross, ema_fast=8, ema_slow=20)
    assert action is None
    assert mode in {"conflict", "wait"}
    assert any(
        s in reason.lower() for s in ("aligned", "ema bull", "no credit", "directional", "sideways")
    )


def _downtrend_frame(n: int = 30) -> pd.DataFrame:
    closes = [130.0 - i for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


def _regime(day_bias: str) -> CprRegime:
    return CprRegime(
        pivot=100.0,
        bc=99.0,
        tc=101.0,
        width=2.0,
        width_pct=2.0,
        width_class="NORMAL",
        cpr_type="BULLISH",
        virgin_cpr=False,
        price_position="above_cpr",
        day_bias=day_bias,
        note="test",
    )


def test_strong_intraday_downtrend_overrides_bull_cpr(monkeypatch) -> None:
    # CPR bias reads bullish (yesterday's close above a thin CPR) but the intraday
    # trend is a clean, confirmed downtrend — sell a bear call spread anyway.
    for k in (
        "SELL_ALLOW_TREND_OVERRIDE",
        "SUPERTREND_PERIOD",
        "SUPERTREND_MULTIPLIER",
        "CREDIT_MIN_VOLUME_RATIO",
        "CREDIT_VOLUME_LOOKBACK_BARS",
    ):
        monkeypatch.delenv(k, raising=False)
    reload_strategy_params()
    frame = _downtrend_frame()
    cross = {"aligned": "bear"}
    action, reason, mode = pick_auto_credit(
        _regime("TRENDING_BULL"), cross, ema_fast=8, ema_slow=20, frame=frame
    )
    assert action == "SELL_BEAR_CALL_SPREAD"
    assert mode == "cpr_trend_override"
    assert "trend-override" in reason


def test_sideways_cpr_still_no_trade_without_confirmed_trend() -> None:
    today, previous = _sideways_frames()
    frame = today.copy()
    frame["volume"] = 1000
    action, _reason, mode = pick_auto_credit(
        analyze_cpr_regime(frame, previous), {"aligned": ""}, ema_fast=8, ema_slow=20, frame=frame
    )
    assert action is None
    assert mode == "wait"


def test_route_auto_sideways_no_iron_condor(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("AUTO_INTELLIGENT_ROUTING", "true")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    reload_strategy_params()
    today, previous = _sideways_frames()
    signal, regime = route_intraday_signal(today, previous, allow_option_selling=True)
    assert regime.day_bias == "SIDEWAYS"
    assert signal.action != "SELL_IRON_CONDOR"
    assert signal.strategy_mode != "cpr_sideways"


def test_tape_veto_blocks_bull_put_when_the_day_is_selling_off(monkeypatch) -> None:
    # The BANKNIFTY 2026-09-08 loss: CPR read TRENDING_BULL off a prior-day pivot,
    # the 1m EMA flickered bull, and a bull-put spread fired while price fell all
    # session. With the tape (candle structure + Supertrend) pointing DOWN, the
    # bullish credit must be vetoed.
    for k in (
        "SUPERTREND_PERIOD",
        "SUPERTREND_MULTIPLIER",
        "CREDIT_MIN_VOLUME_RATIO",
        "CREDIT_VOLUME_LOOKBACK_BARS",
    ):
        monkeypatch.delenv(k, raising=False)
    reload_strategy_params()
    frame = _downtrend_frame()
    action, reason, mode = pick_auto_credit(
        _regime("TRENDING_BULL"), {"aligned": "bull"}, ema_fast=8, ema_slow=20, frame=frame
    )
    assert action is None
    assert mode == "conflict"
    assert "sell puts into it" in reason

    # mirror: a bear-call credit is vetoed on a clean rally
    up = _downtrend_frame()
    up["close"] = up["close"][::-1].to_numpy()
    up["open"] = up["close"]
    up["high"] = up["close"] + 1.0
    up["low"] = up["close"] - 1.0
    action2, reason2, mode2 = pick_auto_credit(
        _regime("TRENDING_BEAR"), {"aligned": "bear"}, ema_fast=8, ema_slow=20, frame=up
    )
    assert action2 is None and mode2 == "conflict" and "sell calls into it" in reason2


def test_trend15_disagreement_blocks_the_trend_override(monkeypatch) -> None:
    # A clean 5m downtrend would trigger a trend-override bear-call, but a 15m read
    # that is still pointing UP must veto it.
    for k in (
        "SUPERTREND_PERIOD",
        "SUPERTREND_MULTIPLIER",
        "CREDIT_MIN_VOLUME_RATIO",
        "CREDIT_VOLUME_LOOKBACK_BARS",
        "SELL_ALLOW_TREND_OVERRIDE",
    ):
        monkeypatch.delenv(k, raising=False)
    reload_strategy_params()
    frame = _downtrend_frame()
    ok, _r, mode = pick_auto_credit(
        _regime("TRENDING_BULL"),
        {"aligned": "bear"},
        ema_fast=8,
        ema_slow=20,
        frame=frame,
        trend15={"direction": -1},
    )
    assert ok == "SELL_BEAR_CALL_SPREAD" and mode == "cpr_trend_override"
    blocked, _r2, _m2 = pick_auto_credit(
        _regime("TRENDING_BULL"),
        {"aligned": "bear"},
        ema_fast=8,
        ema_slow=20,
        frame=frame,
        trend15={"direction": 1},
    )
    assert blocked != "SELL_BEAR_CALL_SPREAD"


def test_summarize_trend15_needs_ema_and_supertrend_to_agree() -> None:
    from index_ai.strategies.strategy_mode import summarize_trend15
    from index_ai.strategies.strategy_params import get_strategy_params

    n = 40
    closes = [100.0 + i for i in range(n)]  # clean uptrend
    up = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=n, freq="15min"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )
    t = summarize_trend15(up, get_strategy_params())
    assert t["direction"] == 1 and t["swing_low"] < t["swing_high"]
    assert summarize_trend15(up.head(5), get_strategy_params())["direction"] == 0
