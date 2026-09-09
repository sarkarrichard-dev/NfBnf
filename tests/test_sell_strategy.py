from __future__ import annotations

import pandas as pd

from index_ai.strategies.sell_strategy import _trend15_block
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.strategies.strategy_router import evaluate_dual_opportunities


def test_trend15_block_direction_and_swing() -> None:
    cfg = get_strategy_params()
    up = {"ready": True, "direction": 1, "structure": "UP", "swing_high": 110.0, "swing_low": 90.0}
    down = {
        "ready": True,
        "direction": -1,
        "structure": "DOWN",
        "swing_high": 110.0,
        "swing_low": 90.0,
    }

    # aligned -> no block
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 100.0, up, cfg) is None
    assert _trend15_block("SELL_BEAR_CALL_SPREAD", 100.0, down, cfg) is None
    # opposed direction -> block
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 100.0, down, cfg) is not None
    assert _trend15_block("SELL_BEAR_CALL_SPREAD", 100.0, up, cfg) is not None
    # price broke the 15m swing the credit leans on -> block
    assert "support" in _trend15_block("SELL_BULL_PUT_SPREAD", 89.0, up, cfg)
    assert "resistance" in _trend15_block("SELL_BEAR_CALL_SPREAD", 111.0, down, cfg)
    # not ready / disabled -> never blocks
    assert _trend15_block("SELL_BULL_PUT_SPREAD", 89.0, {"ready": False}, cfg) is None


def test_dual_opportunities_split_sell_frame_populates_sell_regime(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_STYLE", "AUTO")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    from index_ai.strategies.strategy_params import reload_strategy_params

    reload_strategy_params()
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 90, "close": 105},
            {"open": 105, "high": 112, "low": 88, "close": 108},
        ]
    )
    buy_1m = pd.DataFrame(
        {"open": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "close": [100.0] * 25}
    )
    sell_5m = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-25 09:15", periods=30, freq="5min"),
            "open": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "close": [100.0] * 30,
        }
    )
    trend_15m = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-24 09:15", periods=40, freq="15min"),
            "open": [100.0] * 40,
            "high": [101.0] * 40,
            "low": [99.0] * 40,
            "close": [100.0] * 40,
        }
    )
    dual = evaluate_dual_opportunities(buy_1m, previous, sell_today=sell_5m, sell_trend15=trend_15m)
    assert dual.sell_regime is not None
    assert dual.sell.action in {
        "NO_TRADE",
        "SELL_BULL_PUT_SPREAD",
        "SELL_BEAR_CALL_SPREAD",
        "SELL_IRON_CONDOR",
    }


def _oi(pw, cw, mp=None):
    from index_ai.options_oi import OptionOiContext

    return OptionOiContext(
        spot=0.0,
        atm_strike=0.0,
        total_call_oi=1,
        total_put_oi=1,
        pcr=1.0,
        max_call_oi_strike=cw,
        max_put_oi_strike=pw,
        bias="balanced",
        note="",
        confidence_adjustment=0.0,
        max_pain=mp,
    )


def _sell_signal(frame, oi, **kw):
    from index_ai.strategies.cpr_regime import analyze_cpr_regime
    from index_ai.strategies.sell_strategy import evaluate_sell_signal
    from index_ai.strategies.strategy import add_indicators

    prev = pd.DataFrame([{"open": 100, "high": 110, "low": 90, "close": 100}])
    df = add_indicators(frame, fast=5, slow=10)
    row = df.iloc[-1]
    reg = analyze_cpr_regime(
        df,
        prev,
        price=float(row["close"]),
        ema_fast=float(row["ema_fast"]),
        ema_slow=float(row["ema_slow"]),
    )
    return evaluate_sell_signal(df, prev, reg, oi=oi, **kw)


def test_oi_primary_picks_the_sell_direction():
    flat = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-25 09:15", periods=40, freq="5min"),
            "open": [100.0] * 40,
            "high": [101.0] * 40,
            "low": [99.0] * 40,
            "close": [100.0] * 40,
            "volume": [5000.0] * 40,
        }
    )
    # spot 100, put wall 95, call wall 108 → mid 101.5, leaning the floor → bull put
    sig = _sell_signal(flat, _oi(95.0, 108.0))
    assert sig.action == "SELL_BULL_PUT_SPREAD" and "OI:" in sig.reason
    # jam the walls around spot the other way → bear call
    sig = _sell_signal(flat, _oi(92.0, 101.0))
    assert sig.action == "SELL_BEAR_CALL_SPREAD"
    # spot pinned at max pain → no directional credit
    sig = _sell_signal(flat, _oi(95.0, 108.0, mp=100.0))
    assert sig.action == "NO_TRADE" and "pinned" in sig.reason


def test_breakout_veto_catches_a_still_holding_break():
    from index_ai.strategies.sell_strategy import _breakout_vetoes

    # price sat above the pre-break range for the last few bars — still holding
    closes = [100.0] * 24 + [104.0, 104.5, 104.2]
    frame = pd.DataFrame(
        {
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
        }
    )
    assert "breakout holding" in _breakout_vetoes("SELL_BEAR_CALL_SPREAD", frame)
    assert (
        _breakout_vetoes("SELL_BULL_PUT_SPREAD", frame) == ""
    )  # bull put isn't fighting an up-break

    dn = [100.0] * 24 + [96.0, 95.5, 95.8]
    down = pd.DataFrame({"high": [c + 0.5 for c in dn], "low": [c - 0.5 for c in dn], "close": dn})
    assert "breakdown holding" in _breakout_vetoes("SELL_BULL_PUT_SPREAD", down)
    assert _breakout_vetoes("SELL_BEAR_CALL_SPREAD", down) == ""


def test_oi_path_reapplies_the_tape_veto():
    # a selling-off 5m tape (lower highs/lows) — OI leaning bull-put must be blocked
    closes = [100.0 - i * 0.6 for i in range(30)]
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-05-25 09:15", periods=30, freq="5min"),
            "open": closes,
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
            "volume": [5000.0] * 30,
        }
    )
    sig = _sell_signal(frame, _oi(closes[-1] - 5, closes[-1] + 20))  # walls put spot near the floor
    assert sig.action == "NO_TRADE" and "tape is DOWN" in sig.reason
