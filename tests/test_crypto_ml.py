"""Crypto ML stack — its own dataset, model, gate. No shared code with the index brain."""

from __future__ import annotations

import json

import pandas as pd

from crypto.ml import dataset, gate
from crypto.ml import model as ml_model
from crypto.ml.features import FEATURES, entry_snapshot, label, row_features


def test_features_snapshot_and_vector():
    n = 40
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="1h", tz="UTC"),
            "open": range(n),
            "high": [x + 3 for x in range(n)],
            "low": [x - 3 for x in range(n)],
            "close": range(n),
            "volume": [1.0] * n,
        }
    )
    snap = entry_snapshot("ny_n_break", "SOLUSD", frame, "short")
    assert snap["asset"] == "SOLUSD" and snap["side_long"] == 0.0

    v = row_features({"features": snap, "pnl_usd": 4.0})
    assert set(v) == set(FEATURES)
    assert v["is_solusd"] == 1.0 and v["asset_other"] == 0.0
    assert row_features({"features": {"asset": "ADAUSD"}})["asset_other"] == 1.0
    assert label({"pnl_usd": 4.0}) == 1 and label({"pnl_usd": -1.0}) == 0 and label({}) is None


def _frame(n=40):
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="1h", tz="UTC"),
            "open": range(n),
            "high": [x + 3 for x in range(n)],
            "low": [x - 3 for x in range(n)],
            "close": range(n),
            "volume": [1.0] * n,
        }
    )


_BOOK = {
    "bids": [{"price": 100 - i, "size": 10} for i in range(5)],
    "asks": [{"price": 101 + i, "size": 5} for i in range(5)],
}


def test_microstructure_readings_and_dropped_junk():
    from crypto.ml.features import MICRO_KEYS, microstructure_snapshot

    m = microstructure_snapshot(_BOOK, {"funding_rate": "0.0001", "oi": "1234.5"})
    assert m == {"obi_top5": 0.3333, "funding_rate": 0.0001, "open_interest": 1234.5}
    # an empty/garbled source is left out, never written as 0 ("balanced")
    assert microstructure_snapshot(None, None) == {}
    assert (
        microstructure_snapshot({"bids": [], "asks": []}, {"oi": "x", "funding_rate": None}) == {}
    )
    assert set(MICRO_KEYS) == set(m)


def test_microstructure_does_not_change_the_models_input():
    from crypto.ml.features import microstructure_snapshot

    snap = entry_snapshot("ny_n_break", "BTCUSD", _frame(), "long")
    extra = microstructure_snapshot(_BOOK, {"funding_rate": 0.0001, "oi": 5})
    base = row_features({"features": snap, "pnl_usd": 1.0})
    assert row_features({"features": {**snap, **extra}, "pnl_usd": 1.0}) == base
    assert set(base) == set(FEATURES)


def test_scan_reading_reaches_the_entry_snapshot():
    from crypto import lanes

    lanes._micro.clear()
    try:
        lanes._record_micro("BTCUSD", _BOOK, {"funding_rate": "0.0002", "oi": "10"})
        snap = lanes._entry_features("ny_n_break", "BTCUSD", _frame(), "long")
        assert snap["obi_top5"] == 0.3333 and snap["funding_rate"] == 0.0002
        assert snap["open_interest"] == 10.0 and snap["micro_t"] > 0
        # another symbol with no reading gets none of it
        assert "obi_top5" not in lanes._entry_features("ny_n_break", "ETHUSD", _frame(), "long")
        # a failed fetch drops the old reading instead of letting it go stale
        lanes._record_micro("BTCUSD", None, None)
        assert "obi_top5" not in lanes._entry_features("ny_n_break", "BTCUSD", _frame(), "long")
    finally:
        lanes._micro.clear()


def test_dataset_join_is_chronological(tmp_path, monkeypatch):
    jp = tmp_path / "crypto_journal.jsonl"
    rows = [
        {
            "closed_at": "2026-09-02T00:00:00",
            "mode": "paper",
            "asset": "BTCUSD",
            "pnl_usd": 5.0,
            "features": {"asset": "BTCUSD", "side_long": 1.0, "atr_pct": 0.3},
        },
        {
            "closed_at": "2026-09-01T00:00:00",
            "mode": "live",
            "asset": "ETHUSD",
            "pnl_usd": -2.0,
            "features": {"asset": "ETHUSD", "side_long": 0.0, "atr_pct": 0.5},
        },
        {"closed_at": "2026-09-03T00:00:00", "asset": "BTCUSD"},  # no pnl → dropped
    ]
    jp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(dataset, "JOURNAL_PATH", jp)

    ds = dataset.build_dataset()
    assert ds["total_rows"] == 2 and ds["live_rows"] == 1
    assert [m["when"] for m in ds["meta"]] == sorted(m["when"] for m in ds["meta"])
    assert ds["y"] == [0, 1]  # ETH loss first (earlier), then BTC win


def test_gate_fails_open_without_a_model(monkeypatch):
    monkeypatch.delenv("CRYPTO_ML_GATE", raising=False)
    assert gate.check({"features": {"asset": "BTCUSD"}})["allowed"] is True
    monkeypatch.setenv("CRYPTO_ML_GATE", "true")
    v = gate.check({"features": {"asset": "BTCUSD", "side_long": 1.0}})
    assert v["allowed"] is True and v["armed"] is False  # no trained model → open


def test_train_refuses_under_minimum(tmp_path, monkeypatch):
    jp = tmp_path / "j.jsonl"
    jp.write_text(
        json.dumps(
            {
                "closed_at": "2026-09-01T00:00:00",
                "pnl_usd": 1.0,
                "features": {"asset": "BTCUSD", "side_long": 1.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dataset, "JOURNAL_PATH", jp)
    r = ml_model.train()
    assert r["trained"] is False and "minimum" in r["reason"]


def test_crypto_ml_does_not_import_index_brain():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "crypto" / "ml"
    for src in root.glob("*.py"):
        for line in src.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "index_ai.brain" not in stripped, f"{src.name}: {stripped}"
