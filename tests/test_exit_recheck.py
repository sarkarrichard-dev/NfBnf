"""Exit re-check: classifier, per-segment stats under today's stop distance,
confidence-ladder verdict, stored result, the two endpoints, and the run lock.

Synthetic rows only. conftest.py already points the SQLite journal, the
throwaway .env and the market log at tmp_path; the trades, the exit notes and
the data epoch are patched here so the real journal is never read.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from index_ai import exit_recheck as er
from index_ai import market_log, strategy_lab
from index_ai.charges import leg_charge_rupees
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


# --- India replay on a tmp market log -------------------------------------

DAY = "2026-10-01"
T0 = datetime.fromisoformat(f"{DAY}T10:00:00+05:30")


def _at(i: int) -> datetime:
    """The i-th 30-second tick after the 10:00 entry."""
    return T0 + timedelta(seconds=30 * i)


# climbs 23400 -> 23460 (tick 12), then falls: the 20-pt stop fires on tick 15 (10:07:30), the
# 25-pt stop (23435) on tick 16 (10:08:00), 30-pt on tick 17, 40-pt on tick 18 (10:09:00)
_PATH = (
    [23400.0]
    + [23400.0 + 5 * i for i in range(1, 13)]
    + [23455.0, 23445.0, 23440.0, 23435.0, 23430.0, 23420.0]
    + [23420.0 - 5 * i for i in range(1, 23)]
)


@pytest.fixture
def mlog():
    """A tmp market log (conftest already points market_log.DB_PATH at tmp_path)."""
    ticks, chain = [], []
    for i, spot in enumerate(_PATH):
        t = _at(i)
        if i:
            ticks.append(
                (t.isoformat(), DAY, "NIFTY", 13, "ticker", spot, calendar.timegm(t.timetuple()))
            )
        bid = 100 + 0.5 * (spot - 23400)
        chain.append(
            (t.isoformat(), DAY, "NIFTY", "2026-10-06", spot, 23400.0, "CE", 111, bid, bid, bid + 1)
        )
    with market_log.connect() as con:
        con.executemany(
            "INSERT INTO ticks (ts, session, instrument, security_id, kind, ltp, ltt)"
            " VALUES (?,?,?,?,?,?,?)",
            ticks,
        )
        con.executemany(
            "INSERT INTO chain (ts, session, instrument, expiry, spot, strike, opt_type,"
            " security_id, ltp, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            chain,
        )


def _buy_trade(closed_at: datetime | None, **opt) -> dict:
    option = {
        "quantity": 65,
        "security_id": 111,
        "transaction_type": "BUY",
        "ltp": 101.0,
        "trail_meta": {"entry_index_price": 23400.0},
        **opt,
    }
    if closed_at is not None:
        option["closed_at"] = closed_at.isoformat()
    return {
        "id": "t1",
        "action": "BUY_CALL",
        "instrument": "NIFTY",
        "created_at": T0.isoformat(),
        "pnl": 0.0,
        "option": option,
    }


def _rec(trade: dict, cls: str) -> dict:
    return {"id": trade["id"], "day": DAY, "net": 0.0, "cls": cls, "_trade": trade}


def _real_exit(trade: dict) -> pd.Timestamp:
    return pd.Timestamp(trade["option"]["closed_at"])


def _path():
    with er._market_log_ro() as db:
        return strategy_lab._index_path("NIFTY", DAY, db=db)


def test_india_replay_reproduces_a_trail_exit(mlog):
    trade = _buy_trade(_at(16) + timedelta(seconds=10))  # stop really fired ~10 s after the tick
    ts, how, extended = er._replay_exit(trade, 25.0, _path(), _real_exit(trade), "trail_stop")
    assert how == "trail" and extended is False
    assert ts == pd.Timestamp(_at(16))  # the tick that crossed 23435
    with er._market_log_ro() as db:
        seg = er.replay_segment("buy", "NIFTY", [_rec(trade, "trail_stop")], 25.0, db)
    assert seg["trades_with_ticks"] == 1 and seg["matched"] == 1 and seg["match_rate"] == 1.0


def test_india_replay_respects_a_real_non_stop_exit(mlog):
    path = _path()
    early = _buy_trade(_at(10))  # manual close at 10:05, before the stop would fire
    ts, how, _ = er._replay_exit(early, 25.0, path, _real_exit(early), "manual")
    assert how == "real_exit" and ts == pd.Timestamp(_at(10))
    late = _buy_trade(_at(24))  # manual close at 10:12, four minutes after the replayed stop
    ts, how, _ = er._replay_exit(late, 25.0, path, _real_exit(late), "manual")
    assert how == "trail" and ts == pd.Timestamp(_at(16))
    with er._market_log_ro() as db:
        a = er.replay_segment("buy", "NIFTY", [_rec(early, "manual")], 25.0, db)
        b = er.replay_segment("buy", "NIFTY", [_rec(late, "manual")], 25.0, db)
    assert a["matched"] == 1 and b["matched"] == 0 and b["match_rate"] == 0.0


def test_india_replay_wider_stop_extends_and_flags_rough(mlog):
    trade = _buy_trade(_at(16) + timedelta(seconds=10))
    with er._market_log_ro() as db:
        seg = er.replay_segment("buy", "NIFTY", [_rec(trade, "trail_stop")], 25.0, db)
    assert sorted(seg["by_distance"]) == [20.0, 25.0, 30.0, 40.0]
    assert seg["compared"] == 1 and seg["could_not_price"] == 0
    by = seg["by_distance"]
    assert by[40.0]["extended"] == 1 and by[30.0]["extended"] == 1
    assert by[25.0]["extended"] == 0 and by[20.0]["extended"] == 0
    ts, how, extended = er._replay_exit(trade, 40.0, _path(), _real_exit(trade), "trail_stop")
    assert how == "trail" and extended is True and ts == pd.Timestamp(_at(18))
    # a stop that never fires is held to the 15:10 square-off
    ts, how, extended = er._replay_exit(trade, 500.0, _path(), _real_exit(trade), "trail_stop")
    assert how == "square_off" and extended is True
    assert ts == pd.Timestamp(f"{DAY}T15:10:00+05:30")


def test_india_replay_prices_with_real_quotes_and_charges(mlog):
    with er._market_log_ro() as db:
        q = er._quotes_at(db, "NIFTY", DAY, pd.Timestamp(_at(16) + timedelta(seconds=10)))
        assert q is not None and set(q) == {"111"}
        # newest snapshot <= the exit: tick 16's (spot 23435 -> bid 117.5, ask 118.5)
        assert q["111"]["bid"] == pytest.approx(117.5) and q["111"]["ask"] == pytest.approx(118.5)
        # nothing newer than 300 s -> not priced, never estimated
        assert er._quotes_at(db, "NIFTY", DAY, pd.Timestamp(_at(60))) is None
        assert er._quotes_at(db, "NIFTY", DAY, pd.Timestamp(T0 - timedelta(minutes=5))) is None
    # single bought leg closes at the bid
    buy = _buy_trade(None)
    want = (117.5 - 101.0) * 65
    want -= leg_charge_rupees(101.0, 65, "BUY") + leg_charge_rupees(117.5, 65, "SELL")
    assert er._net_at(buy, q) == pytest.approx(want)
    # a spread: sold leg closes at the ask, bought leg at the bid, SENSEX charged as BSE
    spread = {
        "instrument": "SENSEX",
        "option": {
            "quantity": 20,
            "legs": [
                {"security_id": 2, "transaction_type": "SELL", "ltp": 87.0},
                {"security_id": 3, "transaction_type": "BUY", "ltp": 13.0},
            ],
        },
    }
    quotes = {"2": {"bid": 98.0, "ask": 99.0}, "3": {"bid": 14.5, "ask": 15.5}}
    want = (87.0 - 99.0) * 20 + (14.5 - 13.0) * 20
    for p, side in ((87.0, "SELL"), (99.0, "BUY"), (13.0, "BUY"), (14.5, "SELL")):
        want -= leg_charge_rupees(p, 20, side, exchange="BSE")
    assert er._net_at(spread, quotes) == pytest.approx(want)
    # a leg with no recorded quote: could not price, not an estimate
    assert er._net_at(spread, {"2": quotes["2"]}) is None
    assert er._net_at(spread, {"2": {"bid": 98.0, "ask": None}, "3": quotes["3"]}) is None
    assert er._net_at(buy, None) is None


def test_india_replay_skips_trades_without_close_or_ticks(mlog):
    no_close = _buy_trade(None)
    no_entry_px = _buy_trade(_at(16), trail_meta={})
    no_ticks = _buy_trade(_at(16))
    no_ticks["created_at"] = "2026-10-02T10:00:00+05:30"  # a day with nothing recorded
    recs = [_rec(t, "trail_stop") for t in (no_close, no_entry_px, no_ticks)]
    with er._market_log_ro() as db:
        seg = er.replay_segment("buy", "NIFTY", recs, 25.0, db)
    assert seg["could_not_replay"] == 3 and seg["trades_with_ticks"] == 0
    assert seg["match_rate"] is None and seg["compared"] == 0


def test_index_path_db_param(mlog):
    default = strategy_lab._index_path("NIFTY", DAY)
    assert len(default) == len(_PATH) - 1
    with er._market_log_ro() as db:
        pd.testing.assert_series_equal(strategy_lab._index_path("NIFTY", DAY, db=db), default)


def test_market_log_ro_never_writes(mlog):
    with er._market_log_ro() as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("CREATE TABLE sneaky (a)")
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM chain")
        with pytest.raises(sqlite3.OperationalError):
            db.execute("CREATE INDEX sneaky_ix ON ticks(ltp)")
    with market_log.connect() as con:  # still all there
        assert con.execute("SELECT COUNT(*) FROM chain").fetchone()[0] == len(_PATH)


def test_candidates_round_to_five_points():
    assert er._candidates(25.0) == [20.0, 30.0, 40.0]
    assert er._candidates(40.0) == [30.0, 50.0, 60.0]
    assert er._candidates(55.0) == [40.0, 70.0, 85.0]
    assert er._candidates(1.0) == []  # the distance itself and non-positive values drop


def test_raw_trades_never_reach_the_stored_result(feed):
    feed([_trade(1, "sell", "NIFTY", 3, 100.0, _sell_meta("NIFTY"))])
    assert any("_trade" in r for r in er._india_rows()["india_NIFTY_sell"])  # replay input
    stored = er.run_recheck("button")
    assert "_trade" not in json.dumps(stored, default=str)
    assert "_trade" not in json.dumps(er._load_state(), default=str)
