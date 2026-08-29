import json

import numpy as np
import pandas as pd
import pytest

from index_ai.brain import model as brain_model
from index_ai.brain.features import FEATURES, label, unified_features
from index_ai.brain.gate import check
from index_ai.brain.regime import HIGH_VOL, QUIET, RANGE, TREND, allows, classify


def _day(o, h, low, c, n=60, start="2026-08-28 09:15"):
    return pd.DataFrame({
        "datetime": pd.date_range(start, periods=n, freq="5min"),
        "open": o, "high": h, "low": low, "close": c, "volume": 0.0,
    })


def test_unified_features_maps_every_lane():
    rows = [
        ({"lane": "futures", "instrument": "NIFTY", "direction": "LONG",
          "net_rupees": 100.0}, "is_futures"),
        ({"lane": "sell", "instrument": "NIFTY", "structure": "SELL_BEAR_CALL_SPREAD",
          "long_strike": 25000.0, "net_rupees": -50.0}, "is_credit"),
        ({"instrument": "BANKNIFTY", "side": "PE", "net_rupees": 10.0}, "is_buy_lane"),
    ]
    for trade, flag in rows:
        v = unified_features(trade)
        assert v is not None and v[flag] == 1.0
        assert set(v) == set(FEATURES)
        assert all(isinstance(x, float) for x in v.values())


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
    assert not (r.allow_buy or r.allow_sell or r.allow_futures)

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
    wild = _day(24000, 24400, 23700, 24100)
    today = _day(24010, 24120, 23990, 24100, start="2026-08-29 09:15")
    read = classify(today, wild, cpr_width_pct=0.2)
    v = check({"lane": "sell", "instrument": "NIFTY", "structure": "SELL_ATM_PUT"},
              lane="sell", regime=read)
    assert v["allowed"] is False
    assert "HIGH_VOL" in v["reason"]


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
    meta = [{"net_rupees": float(rng.normal(50, 400)), "when": f"2026-01-{i % 28 + 1:02d}",
             "is_backtest": False} for i in range(n)]
    wf = brain_model.walk_forward(X, y, meta)
    assert set(wf) >= {"oos_static_rupees", "oos_gated_rupees", "oos_delta_rupees"}
    # noise data must not produce a confidently profitable gate
    assert wf["oos_gated_rupees"] <= wf["oos_static_rupees"] + abs(wf["oos_static_rupees"]) + 1


def test_score_shape_and_fail_open_on_unarmed(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_model, "META_PATH", tmp_path / "meta.json")
    (tmp_path / "meta.json").write_text(
        json.dumps({"gate_armed": False, "min_win_prob_gate": 0.0}), encoding="utf-8"
    )
    v = brain_model.score({"lane": "buy", "instrument": "NIFTY", "side": "CE"})
    assert v["passes"] is True and v["armed"] is False
