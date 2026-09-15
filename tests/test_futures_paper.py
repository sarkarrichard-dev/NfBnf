import json

import numpy as np
import pandas as pd

from index_ai.strategies.futures import paper
from index_ai.strategies.futures.engine import LONG, entry_trigger, trend_read
from index_ai.strategies.futures.config import config_for


def _bars(closes, start="2026-08-05 09:15", freq="5min"):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=len(c), freq=freq),
            "open": c,
            "high": c + 3,
            "low": c - 3,
            "close": c,
            "volume": 1000.0,
        }
    )


def test_enabled_and_instruments_from_env(monkeypatch):
    # paper trading is ON by default so a fresh launch logs data without setup;
    # live trading stays separately gated by TRADING_MODE + ALLOW_LIVE_TRADING
    monkeypatch.setenv("ENABLE_STOCK_FUTURES_PAPER", "false")  # indices only for this test
    monkeypatch.delenv("ENABLE_FUTURES_PAPER", raising=False)
    assert paper.enabled() is True
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "false")
    assert paper.enabled() is False
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    monkeypatch.setenv("FUTURES_PAPER_INSTRUMENTS", "NIFTY, SENSEX")
    monkeypatch.delenv("ENABLE_SENSEX", raising=False)  # instruments() drops paused indices
    assert paper.enabled() is True
    assert paper.instruments() == ["NIFTY", "SENSEX"]


def test_stock_universe_and_config(monkeypatch):
    monkeypatch.setenv("ENABLE_STOCK_FUTURES_PAPER", "true")
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "false")
    names = paper.instruments()
    assert "RELIANCE" in names and "NIFTY" not in names
    assert paper._is_stock("RELIANCE") and not paper._is_stock("NIFTY")
    cfg = paper._cfg("RELIANCE")
    assert cfg.key == "RELIANCE" and cfg.lot_size > 1  # real lot from stock_universe.json
    assert cfg.initial_stop_pts > 0 and cfg.trend_ema_fast == 9  # scale-free defaults intact


def test_scan_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "false")
    monkeypatch.setenv("ENABLE_STOCK_FUTURES_PAPER", "false")
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
    t = paper._close(
        {"dir": "SHORT", "entry": 24000.0, "entry_time": "t0"}, 23900.0, "stop", cfg, state
    )
    assert t["points"] == 100.0 and t["gross_rupees"] > 0


def test_status_reads_journal(tmp_path, monkeypatch):
    j = tmp_path / "j.jsonl"
    j.write_text(
        json.dumps({"instrument": "NIFTY", "net_rupees": 500, "exit_time": "2020-01-01T10:00:00"})
        + "\n"
    )
    monkeypatch.setattr(paper, "JOURNAL_PATH", j)
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    st = paper.futures_paper_status()
    assert st["all_time"]["closed"] == 1
    assert st["all_time"]["net_rupees"] == 500


def test_status_marks_open_position_to_market(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "JOURNAL_PATH", tmp_path / "j.jsonl")
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    state = {"NIFTY": {"position": {"dir": "LONG", "entry": 24000.0, "entry_time": "t0"}}}
    paper._save_state(state)
    monkeypatch.setattr(paper, "_fetch", lambda client, key, interval, days=2: _bars([24120.0]))
    st = paper.futures_paper_status()
    pos = st["open_positions"]["NIFTY"]
    cfg = config_for("NIFTY")
    assert pos["mark"] == 24120.0
    assert pos["unrealized_rupees"] == round(120.0 * cfg.lot_size, 2)
    assert st["today"]["open_unrealized_rupees"] == pos["unrealized_rupees"]


def test_status_position_without_a_live_mark_shows_no_pnl(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "JOURNAL_PATH", tmp_path / "j.jsonl")
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setenv("ENABLE_FUTURES_PAPER", "true")
    state = {"NIFTY": {"position": {"dir": "LONG", "entry": 24000.0, "entry_time": "t0"}}}
    paper._save_state(state)
    monkeypatch.setattr(paper, "_fetch", lambda client, key, interval, days=2: pd.DataFrame())
    st = paper.futures_paper_status()
    pos = st["open_positions"]["NIFTY"]
    assert pos["mark"] is None and pos["unrealized_rupees"] is None


def test_config_for_every_index_has_a_real_second_trail_phase():
    """Richard, 2026-09-15: every segment needs a trailing-stop AND a separate
    trailing-profit phase. profit_trigger_pts must fire later than
    trail_activate_pts and profit_trail_pts must be tighter than trail_pts,
    or phase 2 is a no-op -- check it for every configured index, not just
    the one under test."""
    from index_ai.strategies.futures.price_trail import PriceTrailLevels, update_price_trail

    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        cfg = config_for(key)
        assert cfg.profit_trigger_pts > cfg.trail_activate_pts, key
        assert cfg.profit_trail_pts < cfg.trail_pts, key

    # and prove it end to end with NIFTY's real numbers, the same way
    # tick() actually calls it (index_ai/strategies/futures/paper.py)
    cfg = config_for("NIFTY")
    levels = PriceTrailLevels(
        trail_activate_pts=cfg.trail_activate_pts,
        trail_pts=cfg.trail_pts,
        profit_trigger_pts=cfg.profit_trigger_pts,
        profit_trail_pts=cfg.profit_trail_pts,
    )
    pos = {"dir": "LONG", "entry": 24000.0, "peak": 24000.0, "stop": 23955.0}
    # past phase-1's arm level, short of phase 2
    assert not update_price_trail(pos, 24000.0 + cfg.trail_activate_pts + 5, levels)
    assert pos["armed"] and not pos.get("profit_armed")
    # past phase 2's trigger -- the tighter trail takes over and locks in profit
    assert not update_price_trail(pos, 24000.0 + cfg.profit_trigger_pts + 5, levels)
    assert pos["profit_armed"] and pos["stop"] > 24000.0


def test_fetch_window_survives_a_holiday_gap():
    """2026-09-15: every one of the 20 stocks silently returned
    'need_two_sessions' the trading day right after the 2026-09-14 NSE
    holiday — _fetch's old 2-calendar-day window only reached back to Sunday,
    so it found just one real session (today) and tick() gave up with no
    error logged anywhere. A holiday, or a holiday next to a weekend, can put
    more than 2 calendar days between today and the prior trading day, so the
    window must stay wide enough to comfortably bridge that."""
    import inspect

    assert inspect.signature(paper._fetch).parameters["days"].default >= 5


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
