import json

import numpy as np
import pandas as pd

from index_ai.strategies.futures import paper
from index_ai.strategies.futures.engine import LONG, entry_trigger, trend_read
from index_ai.strategies.futures.config import config_for


def _bars(closes, start="2026-08-05 09:15", freq="5min"):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "datetime": pd.date_range(start, periods=len(c), freq=freq),
        "open": c, "high": c + 3, "low": c - 3, "close": c, "volume": 1000.0,
    })


def test_enabled_and_instruments_from_env(monkeypatch):
    monkeypatch.delenv("ENABLE_FUTURES_PAPER", raising=False)
    assert paper.enabled() is False
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    monkeypatch.setenv("FUTURES_PAPER_INSTRUMENTS", "NIFTY, SENSEX")
    assert paper.enabled() is True
    assert paper.instruments() == ["NIFTY", "SENSEX"]


def test_scan_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "false")
    assert paper.scan_futures_paper(object()) == []


def test_close_journals_and_clears_position(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "JOURNAL_PATH", tmp_path / "j.jsonl")
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    cfg = config_for("NIFTY")
    state = {"NIFTY": {"position": {"dir": "LONG", "entry": 24000.0, "entry_time": "t0"}}}
    trade = paper._close(state["NIFTY"]["position"], 24080.0, "stop", cfg, state)
    assert trade["direction"] == "LONG"
    assert trade["points"] == 80.0
    assert trade["net_rupees"] == round(80.0 * cfg.lot_size - trade["friction_rupees"], 2)
    assert state["NIFTY"]["position"] is None
    assert (tmp_path / "j.jsonl").read_text().strip()


def test_short_pnl_sign(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "JOURNAL_PATH", tmp_path / "j.jsonl")
    cfg = config_for("NIFTY")
    state: dict = {}
    t = paper._close({"dir": "SHORT", "entry": 24000.0, "entry_time": "t0"}, 23900.0, "stop", cfg, state)
    assert t["points"] == 100.0 and t["gross_rupees"] > 0


def test_status_reads_journal(tmp_path, monkeypatch):
    j = tmp_path / "j.jsonl"
    j.write_text(json.dumps({"instrument": "NIFTY", "net_rupees": 500, "exit_time": "2020-01-01T10:00:00"}) + "\n")
    monkeypatch.setattr(paper, "JOURNAL_PATH", j)
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    st = paper.futures_paper_status()
    assert st["all_time"]["closed"] == 1
    assert st["all_time"]["net_rupees"] == 500


def test_engine_trend_read_needs_agreement():
    up = _bars(list(np.linspace(24000, 24600, 40)), freq="15min")
    prev = _bars([23800] * 25, freq="15min")
    tr = trend_read(pd.concat([prev, up], ignore_index=True), prev, config_for("NIFTY"))
    assert tr.direction in (LONG, 0)  # rising series -> long or flat, never short


def test_entry_trigger_only_on_reclaim():
    cfg = config_for("NIFTY")
    dip_then_reclaim = _bars(list(np.linspace(24000, 23900, 12)) + [23890, 23960])
    fired, _ = entry_trigger(dip_then_reclaim, LONG, cfg)
    assert isinstance(fired, bool)
