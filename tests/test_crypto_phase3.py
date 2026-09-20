"""Phase 3 crypto — backtest replay + the measured half-spread path."""

from __future__ import annotations

import json
from datetime import datetime, timezone

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


def _ist(y, m, d, h, mi):
    from index_ai.market_clock import IST

    return datetime(y, m, d, h, mi, tzinfo=IST).astimezone(timezone.utc)


def test_funding_crossings_finds_exactly_the_settlements_inside_the_window():
    entry = _ist(2026, 9, 20, 0, 0)
    exit_ = _ist(2026, 9, 20, 8, 0)
    crossings = charges._funding_crossings_utc(entry, exit_)
    assert len(crossings) == 1
    assert crossings[0] == _ist(2026, 9, 20, 5, 30)

    # a window spanning a full day sees all 3 settlements
    assert (
        len(charges._funding_crossings_utc(_ist(2026, 9, 20, 0, 0), _ist(2026, 9, 21, 0, 0))) == 3
    )

    # a tight window with no settlement inside it sees none
    assert charges._funding_crossings_utc(_ist(2026, 9, 20, 6, 0), _ist(2026, 9, 20, 7, 0)) == []


def test_funding_cost_is_zero_without_a_nearby_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(charges, "_FUNDING_SAMPLES_PATH", tmp_path / "f.jsonl")
    cost = charges.funding_cost_usd(
        symbol="BTCUSD",
        side="long",
        notional_usd=1000,
        entry_time=_ist(2026, 9, 20, 0, 0).isoformat(),
        exit_time=_ist(2026, 9, 20, 8, 0).isoformat(),
    )
    assert cost == 0.0


def test_funding_cost_long_pays_short_receives(tmp_path, monkeypatch):
    monkeypatch.setattr(charges, "_FUNDING_SAMPLES_PATH", tmp_path / "f.jsonl")
    charges.sample_funding_rate("BTCUSD", 0.01)  # 1% for this settlement
    kwargs = dict(
        symbol="BTCUSD",
        notional_usd=1000,
        entry_time=_ist(2026, 9, 20, 0, 0).isoformat(),
        exit_time=_ist(2026, 9, 20, 8, 0).isoformat(),
    )
    long_cost = charges.funding_cost_usd(side="long", **kwargs)
    short_cost = charges.funding_cost_usd(side="short", **kwargs)
    assert abs(long_cost - 10.0) < 1e-9  # 1% of $1000, longs pay when rate > 0
    assert abs(short_cost + 10.0) < 1e-9  # shorts receive the same amount


def test_funding_cost_handles_missing_or_malformed_timestamps():
    assert (
        charges.funding_cost_usd(
            symbol="BTCUSD", side="long", notional_usd=1000, entry_time=None, exit_time=None
        )
        == 0.0
    )
    assert (
        charges.funding_cost_usd(
            symbol="BTCUSD",
            side="long",
            notional_usd=1000,
            entry_time="not a date",
            exit_time="also not",
        )
        == 0.0
    )
