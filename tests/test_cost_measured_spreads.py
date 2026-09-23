import json

import index_ai.charges as charges
import index_ai.market_context.spread_calib as sc


def _samples(path, near, wing, n=40):
    rows = [{"at": "2026-09-23T11:00:00+05:30", "instrument": "NIFTY", "bucket": b,
             "half_spread_pts": v} for b, v in (("near", near), ("wing", wing)) for _ in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_slippage_uses_measured_spreads_near_for_sold_wing_for_hedge(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SAMPLES_PATH", tmp_path / "s.jsonl")
    _samples(sc.SAMPLES_PATH, near=0.125, wing=0.025)
    charges._measured_cache.clear()
    spread = {"legs": [{"transaction_type": "SELL", "ltp": 60}, {"transaction_type": "BUY", "ltp": 20}]}
    # (0.125 sold leg + 0.025 hedge) x 65 qty x entry+exit
    assert charges.round_trip_slippage_rupees(spread, 65, "NIFTY") == round(0.15 * 65 * 2, 2)
    naked = {"ltp": 100, "transaction_type": "BUY"}
    assert charges.round_trip_slippage_rupees(naked, 65, "NIFTY") == round(0.125 * 65 * 2, 2)


def test_falls_back_to_defaults_without_enough_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SAMPLES_PATH", tmp_path / "none.jsonl")
    charges._measured_cache.clear()
    naked = {"ltp": 100, "transaction_type": "BUY"}
    assert charges.round_trip_slippage_rupees(naked, 65, "NIFTY") == round(0.75 * 65 * 2, 2)
