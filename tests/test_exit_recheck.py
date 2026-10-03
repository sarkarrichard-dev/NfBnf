"""Exit re-check: classifier, per-segment stats under today's stop distance,
confidence-ladder verdict, stored result, the two endpoints, and the run lock.

Synthetic rows only. conftest.py already points the SQLite journal, the
throwaway .env and the market log at tmp_path; the trades, the exit notes and
the data epoch are patched here so the real journal is never read.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from index_ai import exit_recheck as er
from index_ai.instruments import get_instrument
from index_ai.server import app
from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

INDEXES = ("NIFTY", "BANKNIFTY", "SENSEX")
SELL_ACTION = "SELL_BEAR_CALL_SPREAD"


def _sell_meta(inst: str, pts: float | None = None) -> dict:
    return {"it_points": SELL_TRAIL_POINTS[inst] if pts is None else pts}


def _buy_meta(inst: str, dist: float | None = None, act: float = 0.0) -> dict:
    d = get_instrument(inst).trail_distance_points if dist is None else dist
    return {"trail_distance_points": d, "trail_activation_points": act}


def _trade(tid, lane, inst, day, pnl, meta, qty=0):
    return {
        "id": str(tid),
        "action": "BUY_CALL" if lane == "buy" else SELL_ACTION,
        "instrument": inst,
        "created_at": f"2026-11-{day:02d}T10:00:00+05:30",
        "pnl": pnl,
        "mode": "PAPER",
        "signal": {},
        "option": {"quantity": qty, "trail_meta": meta},
    }


@pytest.fixture
def feed(monkeypatch):
    """Patch the journal feed; returns a setter taking (trades, notes, epoch)."""
    import index_ai.day_review as dr
    import index_ai.learning as learning

    def _set(trades, notes=None, epoch=None):
        monkeypatch.setattr(er, "data_epoch", lambda: epoch)
        monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)
        monkeypatch.setattr(dr, "_exit_notes", lambda: notes or {})

    _set([])
    return _set


def _seg(result, inst, lane):
    return next(s for s in result["segments"] if s["segment"] == f"india_{inst}_{lane}")


# --- classifier -----------------------------------------------------------

REAL_NOTES = {
    "trail_stop": [
        "Index trail: 24500.0 crossed the stop 24520.0 (40 pts behind best 24560.0; 20 pts from entry).",
        "Trailing stop armed — 25 index pts behind peak at 24580.0",
        "Initial stop (25 index pts) hit at 24475.0",
    ],
    "time_exit": [
        "End-of-session square-off (IST)",
        "Missed 15:10 square-off — flat at scanner (IST)",
    ],
    "manual": ["manual close"],
    "signal_exit": [
        "CPR regime TRENDING_BULL — closing bearish Buy Put.",
        "AUTO: CPR flipped bullish",
        "AUTO: EMA cross against the position",
        "Strategy signal SELL_BULL_PUT_SPREAD — closing the opposite spread.",
        "Price 24500 above Supertrend stop 24480",
    ],
    "other_rule": [
        "Index 24400 below short put 24450.",
        "Credit max loss reached: -4000",
        "Credit stop loss: premium doubled",
        "Profit trail: gave back to floor 500 (fell to floor)",
        "Credit profit target hit: 50% of credit",
        "Iron condor: index 24600 breached the short call",
    ],
    "legacy_premium": [
        "Hard stop: premium moved 40% against the trade",
        "Trailing exit: premium rose then fell 12% from peak",
        "Trailing exit: premium fell below the trail",
    ],
    "other": ["", None, "Something nobody has ever written before"],
}


def test_exit_classifier():
    for want, notes in REAL_NOTES.items():
        for note in notes:
            assert er.classify_exit("india", note) == want, (want, note)


# --- segments -------------------------------------------------------------


def test_segments(feed, monkeypatch):
    notes = {"1": "Index trail: x crossed the stop y (...)", "2": "manual close"}
    trades = [
        _trade(1, "sell", "NIFTY", 3, 1000.0, _sell_meta("NIFTY")),  # counts
        _trade(2, "sell", "NIFTY", 3, -400.0, _sell_meta("NIFTY")),  # counts, same day
        _trade(3, "sell", "NIFTY", 4, 900.0, _sell_meta("NIFTY", 100.0)),  # other distance
        _trade(4, "buy", "NIFTY", 5, 200.0, _buy_meta("NIFTY")),  # counts
        _trade(5, "buy", "NIFTY", 5, 200.0, _buy_meta("NIFTY", 40.0, 25.0)),  # old tune
        _trade(6, "buy", "NIFTY", 6, None, _buy_meta("NIFTY")),  # still open
        _trade(7, "buy", "NIFTY", 1, 50.0, _buy_meta("NIFTY")),  # before the epoch
    ]
    feed(trades, notes, epoch="2026-11-02T00:00:00+05:30")
    out = er.compute_segments()
    segs = out["segments"]
    assert len(segs) == 6 and out["errors"] == []
    assert {s["segment"] for s in segs} == {
        f"india_{i}_{lane}" for i in INDEXES for lane in ("buy", "sell")
    }
    sell = _seg(out, "NIFTY", "sell")
    assert sell["trades"] == 2 and sell["trading_days"] == 1
    assert sell["wins"] == 1 and sell["win_rate"] == 0.5
    assert sell["trail_hits"] == 1 and sell["trail_hit_rate"] == 0.5
    assert sell["net"] == 600.0
    assert sell["exit_mix"] == {"trail_stop": 1, "manual": 1}
    assert sell["distance"] == SELL_TRAIL_POINTS["NIFTY"]
    buy = _seg(out, "NIFTY", "buy")
    assert buy["trades"] == 1 and buy["trading_days"] == 1
    for inst, lane in (("BANKNIFTY", "buy"), ("SENSEX", "sell")):  # empty rows still present
        s = _seg(out, inst, lane)
        assert s["trades"] == 0 and s["win_rate"] is None and s["verdict"] == "not_enough_data"

    # net of charges decides the win, not the gross
    monkeypatch.setattr(er, "_india_charges", lambda t: (30.0, 20.0))
    feed([_trade(1, "sell", "NIFTY", 3, 40.0, _sell_meta("NIFTY"))])
    s = _seg(er.compute_segments(), "NIFTY", "sell")
    assert s["net"] == -10.0 and s["wins"] == 0


# --- ladder verdict -------------------------------------------------------


def _n_trades(n, days, pnl):
    # newest first, spread over `days` distinct dates
    return [_trade(i, "sell", "NIFTY", 1 + (i % days), pnl, _sell_meta("NIFTY")) for i in range(n)]


@pytest.mark.parametrize("n,days", [(39, 20), (40, 14)])
def test_verdict_ladder_not_enough(feed, n, days):
    feed(_n_trades(n, days, 100.0))
    s = _seg(er.compute_segments(), "NIFTY", "sell")
    assert s["verdict"] == "not_enough_data" and s["suggestion"] is None
    assert s["trades"] == n and s["trading_days"] == days
    assert s["win_rate"] == 1.0 and s["net"] == 100.0 * n  # real numbers still shown
    assert str(n) in s["message"]


def test_verdict_ladder_ready(feed):
    feed(_n_trades(40, 15, 100.0))
    s = _seg(er.compute_segments(), "NIFTY", "sell")
    assert s["state"] == "ready" and s["frozen"] is True
    assert s["verdict"] == "working" and s["suggestion"] is None
    feed(_n_trades(40, 15, -100.0))
    s = _seg(er.compute_segments(), "NIFTY", "sell")
    assert s["state"] == "ready" and s["frozen"] is False
    assert s["verdict"] == "no_replay_data" and s["suggestion"] is None


def test_verdict_ladder_frozen_ignored_below_bar(feed):
    feed(_n_trades(10, 5, 100.0))  # net-positive but far below the bar
    s = _seg(er.compute_segments(), "NIFTY", "sell")
    assert s["verdict"] == "not_enough_data"


# --- endpoints ------------------------------------------------------------


def test_endpoints(feed, tmp_path):
    import index_ai.config as cfg

    sell_before = dict(SELL_TRAIL_POINTS)
    buy_before = {i: get_instrument(i).trail_distance_points for i in INDEXES}
    feed([_trade(1, "sell", "NIFTY", 3, 100.0, _sell_meta("NIFTY"))])
    client = TestClient(app)

    first = client.get("/api/exit-recheck").json()
    assert first["ran_at"] is None and first["segments"] == []

    r = client.post("/api/exit-recheck/run")
    assert r.status_code == 200
    body = r.json()
    assert body["trigger"] == "button" and len(body["segments"]) == 6

    again = client.get("/api/exit-recheck").json()
    assert again["ran_at"] == body["ran_at"]
    fresh = er._load_state()  # a restart is just a fresh read of the row
    assert fresh["ran_at"] == body["ran_at"] and fresh["segments"] == body["segments"]

    assert SELL_TRAIL_POINTS == sell_before
    assert {i: get_instrument(i).trail_distance_points for i in INDEXES} == buy_before
    assert cfg.ENV_PATH.read_text() == ""


def test_run_recheck_rejects_unknown_trigger():
    with pytest.raises(ValueError):
        er.run_recheck("whenever")


# --- lock -----------------------------------------------------------------


def test_run_recheck_waits_when_busy(feed, monkeypatch):
    feed([])
    stored = er.run_recheck("daily")
    calls = []
    real = er.compute_segments
    monkeypatch.setattr(er, "compute_segments", lambda: calls.append(1) or real())

    result = {}
    er._RECHECK_LOCK.acquire()
    try:
        t = threading.Thread(target=lambda: result.update(er.run_recheck("button")))
        t.start()
        time.sleep(0.3)
        assert t.is_alive()  # blocked behind the running pass
    finally:
        er._RECHECK_LOCK.release()
    t.join(timeout=5)
    assert not t.is_alive()
    assert calls == []  # waited, did not recompute
    assert result["ran_at"] == stored["ran_at"] and result["trigger"] == "daily"
