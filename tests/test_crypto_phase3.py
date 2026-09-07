"""Phase 3 crypto — backtest replay + the measured half-spread path."""

from __future__ import annotations

import json

import pandas as pd

from crypto import backtest, charges


def _wave(n: int, freq: str, base: float) -> pd.DataFrame:
    # a rising sawtooth: repeated runs up then a sharp drop — gives the strategies
    # something to enter and exit on
    closes = []
    v = base
    for i in range(n):
        v += base * 0.003 if i % 40 < 30 else -base * 0.01
        closes.append(v)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
            "open": closes,
            "high": [c + base * 0.002 for c in closes],
            "low": [c - base * 0.002 for c in closes],
            "close": closes,
            "volume": [100.0] * n,
        }
    )


def test_backtest_runs_and_summarises(monkeypatch):
    monkeypatch.setattr(backtest, "_WINDOW", 100)

    def fake_candles(symbol, resolution, *, days=3.0, client=None):
        base = 60_000.0 if symbol == "BTCUSD" else 3_000.0
        freq = "5min" if resolution == "5m" else "1h"
        return _wave(340, freq, base)

    monkeypatch.setattr(backtest.market_data, "candles", fake_candles)
    monkeypatch.setattr(backtest.products, "get", lambda sym, client=None: None)  # use fallback CV

    res = backtest.run(days=30, assets=["BTCUSD"], strategies=["ny_n_break", "ichimoku"])
    s = res.summary()
    assert s["trades"] >= 0
    if s["trades"]:
        assert "net_usd" in s and "win_rate" in s and "max_drawdown_usd" in s
        assert set(s["by"]) <= {"ny_n_break:BTCUSD", "ichimoku:BTCUSD"}


def test_measured_half_spread_beats_fallback(tmp_path, monkeypatch):
    p = tmp_path / "samples.jsonl"
    monkeypatch.setattr(charges, "_SAMPLES_PATH", p)
    # too few samples → fallback
    assert charges.measured_half_spread_bps("BTCUSD") is None
    with p.open("w", encoding="utf-8") as fh:
        for _ in range(40):
            fh.write(json.dumps({"symbol": "BTCUSD", "half_bps": 0.4}) + "\n")
    assert charges.measured_half_spread_bps("BTCUSD") == 0.4
    # half_spread_usd now uses the measured 0.4 bps, not the 1.0 bps BTC fallback
    assert abs(charges.half_spread_usd("BTCUSD", 60_000) - 60_000 * 0.4 / 10_000) < 1e-9


def test_sample_spread_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(charges, "_SAMPLES_PATH", tmp_path / "s.jsonl")
    charges.sample_spread("BTCUSD", None)
    charges.sample_spread("BTCUSD", {"bids": [{"price": 100.0}], "asks": [{"price": 100.2}]})
    rows = (tmp_path / "s.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["half_bps"] > 0
