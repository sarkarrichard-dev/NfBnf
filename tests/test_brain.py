import json

import numpy as np
import pandas as pd
import pytest

from index_ai.brain import model as brain_model
from index_ai.brain.features import FEATURES, label, unified_features
from index_ai.brain.gate import check
from index_ai.brain.regime import HIGH_VOL, QUIET, RANGE, TREND, allows, classify


def _day(o, h, low, c, n=60, start="2026-08-28 09:15"):
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=n, freq="5min"),
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": 0.0,
        }
    )


def test_unified_features_maps_every_lane():
    rows = [
        (
            {"lane": "futures", "instrument": "NIFTY", "direction": "LONG", "net_rupees": 100.0},
            "is_futures",
        ),
        (
            {
                "lane": "sell",
                "instrument": "NIFTY",
                "structure": "SELL_BEAR_CALL_SPREAD",
                "long_strike": 25000.0,
                "net_rupees": -50.0,
            },
            "is_credit",
        ),
        ({"instrument": "BANKNIFTY", "side": "PE", "net_rupees": 10.0}, "is_buy_lane"),
    ]
    for trade, flag in rows:
        v = unified_features(trade)
        assert v is not None and v[flag] == 1.0
        assert set(v) == set(FEATURES)
        assert all(isinstance(x, float) for x in v.values())


def test_option_greeks_feed_the_win_probability_model():
    """2026-09-18: delta/IV/gamma already pick *which* strike gets bought
    (PR #115/#119) but were never fed to the win-probability model, which
    only reasoned about the signal. A trade with real Greeks on its option
    dict must surface them as real feature values; a trade without (every
    trade before this shipped, or a non-buy-lane trade) must read as an
    inert 0.0, not crash or invent a number."""
    with_greeks = {
        "instrument": "NIFTY",
        "action": "BUY_CALL",
        "signal": {"price": 23300.0},
        "option": {
            "ltp": 120.0,
            "delta": -0.42,
            "gamma": 0.0013,
            "theta": -18.0,
            "iv": 11.5,
            "max_pain": 23200.0,
        },
        "net_rupees": 400.0,
    }
    v = unified_features(with_greeks)
    assert set(v) == set(FEATURES)
    assert v["option_delta"] == 0.42  # magnitude, sign doesn't matter
    assert v["option_gamma"] == 0.0013
    assert abs(v["option_theta_drag_pct"] - 15.0) < 1e-6  # 18/120 * 100
    assert v["option_iv"] == 11.5
    assert abs(v["dist_max_pain_pct"] - (100 / 23300 * 100)) < 1e-6

    without_greeks = {"instrument": "NIFTY", "action": "BUY_CALL", "net_rupees": -100.0}
    v2 = unified_features(without_greeks)
    assert v2["option_delta"] == 0.0
    assert v2["option_gamma"] == 0.0
    assert v2["option_theta_drag_pct"] == 0.0
    assert v2["option_iv"] == 0.0
    assert v2["dist_max_pain_pct"] == 0.0


def test_label_needs_a_closed_trade():
    assert label({"net_rupees": 1.0}) == 1
    assert label({"pnl": -1.0}) == 0
    assert label({"entry_premium": 100}) is None


def test_regime_stands_down_on_high_vol_and_quiet():
    wild = _day(24000, 24400, 23700, 24100)
    calm = _day(24000, 24060, 23960, 24010)
    today = _day(24010, 24120, 23990, 24100, start="2026-08-29 09:15")

    r = classify(today, wild, cpr_width_pct=0.2)
    assert r.regime == HIGH_VOL
    # HIGH_VOL stands futures down and allows directional buying — range
    # expansion is what a long option is paid for. Selling still runs too
    # (Richard, 2026-09-11), just on a tighter stop (tighten_sell_stop).
    assert r.allow_buy and r.allow_sell and r.tighten_sell_stop and not r.allow_futures

    flat = _day(24010, 24030, 23995, 24010, start="2026-08-29 09:15")
    assert classify(flat, calm, cpr_width_pct=0.7).regime == QUIET


def test_regime_range_day_allows_sell_only():
    normal = _day(24000, 24140, 23880, 24010)
    flat = _day(24010, 24030, 23995, 24010, start="2026-08-29 09:15")
    r = classify(flat, normal, cpr_width_pct=0.7)
    assert r.regime == RANGE
    assert r.allow_sell and not r.allow_buy and not r.allow_futures
    assert allows(r, "sell") and not allows(r, "buy")


def test_regime_trend_day_allows_all():
    calm = _day(24000, 24140, 23900, 24010)
    trendy = _day(24010, 24120, 23990, 24100, start="2026-08-29 09:15")
    r = classify(trendy, calm, cpr_width_pct=0.2)
    assert r.regime == TREND and r.allow_buy and r.allow_futures


def test_gate_fails_open_when_model_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_model, "MODEL_PATH", tmp_path / "none.joblib")
    monkeypatch.setattr(brain_model, "META_PATH", tmp_path / "none.json")
    v = check({"lane": "futures", "instrument": "NIFTY", "direction": "LONG"}, lane="futures")
    assert v["allowed"] is True
    assert v["win_probability"] is None


def test_gate_blocks_on_stood_down_regime():
    # HIGH_VOL no longer stands the sell lane down (it trades on a tighter
    # stop instead — see test_regime_stands_down_on_high_vol_and_quiet), so
    # QUIET is the regime that still fully stands a lane down here.
    calm = _day(24000, 24060, 23960, 24010)
    flat = _day(24010, 24030, 23995, 24010, start="2026-08-29 09:15")
    read = classify(flat, calm, cpr_width_pct=0.7)
    assert read.regime == QUIET
    v = check(
        {"lane": "sell", "instrument": "NIFTY", "structure": "SELL_ATM_PUT"},
        lane="sell",
        regime=read,
    )
    assert v["allowed"] is False
    assert "QUIET" in v["reason"]


def test_gate_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ENABLE_BRAIN_GATE", "false")
    wild = _day(24000, 24400, 23700, 24100)
    today = _day(24010, 24120, 23990, 24100, start="2026-08-29 09:15")
    read = classify(today, wild, cpr_width_pct=0.2)
    assert check({"lane": "sell"}, lane="sell", regime=read)["allowed"] is True


def test_walk_forward_refuses_to_arm_when_gate_loses_money(tmp_path, monkeypatch):
    """The core honesty rule: a gate that hurts out-of-sample must not arm."""
    pytest.importorskip("sklearn")
    monkeypatch.setattr(brain_model, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(brain_model, "MODEL_PATH", tmp_path / "m.joblib")
    monkeypatch.setattr(brain_model, "META_PATH", tmp_path / "m.json")

    rng = np.random.default_rng(0)
    n = 120
    # features carry no signal, so any threshold only removes random trades
    X = rng.normal(size=(n, len(FEATURES)))
    y = rng.integers(0, 2, size=n)
    meta = [
        {
            "net_rupees": float(rng.normal(50, 400)),
            "when": f"2026-01-{i % 28 + 1:02d}",
            "is_backtest": False,
        }
        for i in range(n)
    ]
    wf = brain_model.walk_forward(X, y, meta)
    assert set(wf) >= {"oos_static_rupees", "oos_gated_rupees", "oos_delta_rupees"}
    # noise data must not produce a confidently profitable gate
    assert wf["oos_gated_rupees"] <= wf["oos_static_rupees"] + abs(wf["oos_static_rupees"]) + 1


def test_immature_features_are_suppressed_until_they_have_real_support():
    """2026-09-18 safety review: a feature added after the model was already
    trading (the option-Greeks columns) must not be allowed to fit a
    coefficient off a handful of real rows -- it needs its own minimum
    support first, same as MIN_LIVE_ROWS/MIN_CLASS_ROWS gate the model as a
    whole. Below the threshold it must read as if the column never existed."""
    n = 50
    X = np.zeros((n, len(FEATURES)))
    delta_idx = FEATURES.index("option_delta")
    gamma_idx = FEATURES.index("option_gamma")
    # only 3 rows carry a real (non-zero) delta -- below the 15-row minimum
    X[:3, delta_idx] = [0.42, 0.51, 0.38]
    # 20 rows carry a real gamma -- above the minimum, must NOT be suppressed
    X[:20, gamma_idx] = np.linspace(0.001, 0.002, 20)

    out, suppressed = brain_model._suppress_immature_features(X)
    assert "option_delta" in suppressed
    assert "option_gamma" not in suppressed
    assert np.count_nonzero(out[:, delta_idx]) == 0  # zeroed out entirely
    assert np.count_nonzero(out[:, gamma_idx]) == 20  # left alone
    assert X is not out  # never mutates the caller's array in place


def test_train_suppresses_and_reports_immature_greeks_on_a_thin_live_mix(tmp_path, monkeypatch):
    """End-to-end: train() on a realistic thin mix (mostly zero-Greek rows,
    a few real ones) must suppress the immature columns and say so in the
    persisted meta, not silently fit a coefficient nobody can see."""
    pytest.importorskip("sklearn")
    monkeypatch.setattr(brain_model, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(brain_model, "MODEL_PATH", tmp_path / "m.joblib")
    monkeypatch.setattr(brain_model, "META_PATH", tmp_path / "m.json")

    rng = np.random.default_rng(1)
    n = 63
    X = rng.normal(size=(n, len(FEATURES))).clip(-1, 1)
    delta_idx = FEATURES.index("option_delta")
    X[:, delta_idx] = 0.0
    X[:3, delta_idx] = [0.42, 0.51, 0.38]  # only 3 real rows, like the live data today
    y = rng.integers(0, 2, size=n)
    meta = [{"net_rupees": float(rng.normal(0, 300)), "is_backtest": False} for _ in range(n)]

    fake_ds = {
        "X": X.tolist(),
        "y": y.tolist(),
        "meta": meta,
        "total_rows": n,
        "live_rows": n,
        "counts": {"fake": n},
    }
    monkeypatch.setattr("index_ai.brain.store.build_dataset", lambda **kw: fake_ds)

    result = brain_model.train(force=True)
    assert result["trained"]
    assert "option_delta" in result["suppressed_features"]
    assert result["coefficients"]["option_delta"] == 0.0


def test_score_shape_and_fail_open_on_unarmed(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_model, "META_PATH", tmp_path / "meta.json")
    (tmp_path / "meta.json").write_text(
        json.dumps({"gate_armed": False, "min_win_prob_gate": 0.0}), encoding="utf-8"
    )
    v = brain_model.score({"lane": "buy", "instrument": "NIFTY", "side": "CE"})
    assert v["passes"] is True and v["armed"] is False
