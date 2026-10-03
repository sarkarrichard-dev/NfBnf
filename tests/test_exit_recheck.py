"""Exit re-check: classifier, per-segment stats under today's stop distance,
confidence-ladder verdict, stored result, the two endpoints, and the run lock.

Synthetic rows only. conftest.py already points the SQLite journal, the
throwaway .env and the market log at tmp_path; the trades, the exit notes and
the data epoch are patched here so the real journal is never read.
"""

from __future__ import annotations

import calendar
import contextlib
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
def feed(monkeypatch, tmp_path):
    """Patch the journal feed; returns a setter taking (trades, notes, epoch)."""
    import index_ai.day_review as dr
    import index_ai.learning as learning

    # the crypto and commodity journals are read by every recheck: never the real ones
    monkeypatch.setattr("crypto.journal.JOURNAL_PATH", tmp_path / "no_crypto.jsonl")
    monkeypatch.setattr("commodities.lanes.JOURNAL_PATH", tmp_path / "no_commodity.jsonl")

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


# Real shapes from memory/crypto_journal.jsonl and memory/commodity_journal.jsonl (numbers kept).
CRYPTO_REASONS = {
    "trail_stop": [
        "point trail: 1.88926 pts behind best 117.639 (stop 119.528, -1.44892 pts from entry)"
    ],
    "other_rule": [
        "trailing stop 24% P&L (peak 3.1%, now 2.0%)",
        "trailing profit 15% P&L (peak 40%, now 30%)",
        "take_profit",
        "stop_loss",
    ],
    "manual": ["manual close", "coin removed"],
    "time_exit": ["session end", "1-day max hold", "3-day max hold"],
    "signal_exit": [
        "close 61000 back through SMA20 high band 60800",
        "close 61000 back through SMA20 low band 61200",
        "trend flip",
        "15m N",
        "15m inverted-N",
        "Close 2.5 back above Kijun 2.4.",
        "Close 2.5 back into cloud (top 2.6).",
        "RSI + EMA rolled over with ADX confirming — momentum reversed",
    ],
    "other": ["", None, "something crypto has never said"],
}
COMMODITY_REASONS = {
    "trail_stop": ["stop"],
    "manual": ["manual close"],
    "time_exit": ["square_off"],
    "signal_exit": ["trend_flip"],
    "other": ["", None, "mystery"],
}


def test_exit_classifier():
    for want, notes in REAL_NOTES.items():
        for note in notes:
            assert er.classify_exit("india", note) == want, (want, note)
    for want, notes in CRYPTO_REASONS.items():
        for note in notes:
            assert er.classify_exit("crypto", note) == want, (want, note)
    for want, notes in COMMODITY_REASONS.items():
        for note in notes:
            assert er.classify_exit("commodity", note) == want, (want, note)


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
    assert len(segs) == 8 and out["errors"] == []  # six India + crypto + commodities
    assert {s["segment"] for s in segs} == {
        f"india_{i}_{lane}" for i in INDEXES for lane in ("buy", "sell")
    } | {"crypto:point_trail", "commodities:atr_trail"}
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
    # ready and not frozen now goes through the replay gate (no price log here -> unreliable)
    assert s["verdict"] == "replay_unreliable" and s["suggestion"] is None


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
    assert body["trigger"] == "button" and len(body["segments"]) == 8

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
    monkeypatch.setattr(er, "compute_segments", lambda *a, **k: calls.append(1) or real(*a, **k))

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


# --- suggestion gate in the India verdict ---------------------------------

SUGGESTED_END = "Nothing was changed — this is only a suggestion for you to approve."


def _canned(
    matched=85,
    with_ticks=100,
    compared=40,
    nets=None,
    wins=None,
    extended=None,
    distance=40.0,
):
    """A replay_segment() result for NIFTY sell (today 40 points; tries 30, 50, 60)."""
    nets = nets or {40.0: -4000.0, 30.0: -3000.0, 50.0: -5000.0, 60.0: -4500.0}
    wins = wins or {40.0: 12, 30.0: 18, 50.0: 10, 60.0: 9}
    extended = extended or {}
    return {
        "distance": distance,
        "trades_with_ticks": with_ticks,
        "matched": matched,
        "match_rate": matched / with_ticks if with_ticks else None,
        "could_not_replay": 0,
        "could_not_price": 40 - compared,
        "compared": compared,
        "by_distance": {
            d: {"net": n, "wins": wins[d], "extended": extended.get(d, 0)} for d, n in nets.items()
        },
    }


@pytest.fixture
def replay(feed, monkeypatch):
    """A ready, not-frozen NIFTY sell segment whose replay is canned; records every call."""
    feed(_n_trades(40, 15, -100.0))
    box: dict = {"result": _canned()}
    calls: list = []

    def fake(lane, inst, records, distance, db, **kw):
        calls.append((lane, inst, distance, len(records)))
        if isinstance(box["result"], Exception):
            raise box["result"]
        return box["result"]

    monkeypatch.setattr(er, "_market_log_ro", lambda: contextlib.nullcontext())
    monkeypatch.setattr(er, "replay_segment", fake)
    return box, calls


def _nifty_sell():
    return _seg(er.compute_segments(), "NIFTY", "sell")


@pytest.mark.parametrize("n,days", [(39, 20), (40, 14)])
def test_gate_not_ready_never_opens_the_replay(feed, monkeypatch, n, days):
    def boom(*a, **k):
        raise AssertionError("replay must not run below the ladder bar")

    monkeypatch.setattr(er, "replay_segment", boom)
    monkeypatch.setattr(er, "_market_log_ro", boom)
    feed(_n_trades(n, days, -100.0))
    s = _nifty_sell()
    assert s["verdict"] == "not_enough_data" and s["suggestion"] is None


def test_gate_frozen_is_working_and_never_replayed(feed, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a net-positive segment must never be replayed")

    monkeypatch.setattr(er, "replay_segment", boom)
    monkeypatch.setattr(er, "_market_log_ro", boom)
    feed(_n_trades(40, 15, 100.0))
    s = _nifty_sell()
    assert s["state"] == "ready" and s["frozen"] is True
    assert s["verdict"] == "working" and s["suggestion"] is None


def test_gate_match_rate_boundary(replay):
    box, calls = replay
    box["result"] = _canned(matched=79)  # 0.79
    s = _nifty_sell()
    assert s["verdict"] == "replay_unreliable" and s["suggestion"] is None
    assert "79 of 100 real exits" in s["message"]
    box["result"] = _canned(matched=80)  # exactly 0.8 passes
    s = _nifty_sell()
    assert s["verdict"] == "suggestion" and s["suggestion"] is not None
    assert len(calls) == 2  # one replay per verdict, only for the one ready segment


def test_gate_needs_forty_priced_trades(replay):
    box, _ = replay
    box["result"] = _canned(compared=39)
    s = _nifty_sell()
    assert s["verdict"] == "replay_unreliable" and s["suggestion"] is None
    assert "39 of the last 40 trades" in s["message"]
    box["result"] = _canned(compared=40)
    assert _nifty_sell()["verdict"] == "suggestion"


def test_gate_no_trade_could_be_replayed(replay):
    box, _ = replay
    box["result"] = _canned(matched=0, with_ticks=0, compared=0)
    s = _nifty_sell()
    assert s["verdict"] == "replay_unreliable" and s["suggestion"] is None


def test_gate_no_candidate_beats_today(replay):
    box, _ = replay
    # one ties today's net, the rest are worse: strictly better is required
    box["result"] = _canned(nets={40.0: -4000.0, 30.0: -4000.0, 50.0: -5000.0, 60.0: -4500.0})
    s = _nifty_sell()
    assert s["verdict"] == "no_better_distance" and s["suggestion"] is None
    assert "30, 50, 60" in s["message"] and "40-point" in s["message"]


def test_gate_tiny_gain_is_not_a_suggestion(replay):
    box, _ = replay
    # 499 better is noise; exactly 500 better is enough
    box["result"] = _canned(nets={40.0: -4000.0, 30.0: -3501.0, 50.0: -5000.0, 60.0: -4500.0})
    s = _nifty_sell()
    assert s["verdict"] == "no_better_distance" and s["suggestion"] is None
    box["result"] = _canned(nets={40.0: -4000.0, 30.0: -3500.0, 50.0: -5000.0, 60.0: -4500.0})
    s = _nifty_sell()
    assert s["verdict"] == "suggestion" and s["suggestion"]["extra_net"] == 500.0


def test_not_enough_data_message_says_what_is_missing():
    row = {
        "state": "observing",
        "trades": 49,
        "trading_days": 8,
        "distance_label": "1.6% trail",
        "venue": "crypto",
    }
    verdict, msg = er._verdict(row, [])
    assert verdict == "not_enough_data"
    assert "needs 40 trades over at least 15 trading days" in msg
    assert "49 trades over 8 trading days" in msg


def test_gate_suggestion_text_and_numbers(replay):
    s = _nifty_sell()
    assert s["verdict"] == "suggestion"
    msg = s["message"]
    assert "NIFTY sell" in msg and "40-point stop" in msg and "too loose" in msg
    assert "30 points would have kept ₹1,000 more over the last 40 trades" in msg
    assert "win rate 30% -> 45%" in msg
    assert msg.endswith(SUGGESTED_END)
    assert "rough" not in msg
    assert s["suggestion"] == {
        "distance": 30.0,
        "current": 40.0,
        "extra_net": 1000.0,
        "trades": 40,
        "win_rate_now": 0.3,
        "win_rate_alt": 0.45,
        "match_rate": 0.85,
        "rough": False,
    }


def test_gate_wider_stop_with_held_trades_says_the_figure_is_rough(replay):
    box, _ = replay
    box["result"] = _canned(
        nets={40.0: -4000.0, 30.0: -4100.0, 50.0: -2400.0, 60.0: -4500.0},
        wins={40.0: 12, 30.0: 11, 50.0: 21, 60.0: 9},
        extended={50.0: 6},
    )
    s = _nifty_sell()
    assert s["verdict"] == "suggestion" and s["suggestion"]["rough"] is True
    assert "too tight" in s["message"] and "50 points would have kept ₹1,600 more" in s["message"]
    assert "rough" in s["message"] and s["message"].endswith(SUGGESTED_END)


def test_gate_unreadable_prices_never_raise(replay, feed):
    box, _ = replay
    box["result"] = RuntimeError("database is locked")
    s = _nifty_sell()
    assert s["verdict"] == "replay_unreliable" and s["suggestion"] is None
    assert "could not read the recorded prices" in s["message"].lower()
    assert er.run_recheck("button")["errors"] == []  # nothing escapes run_recheck


def test_gate_missing_price_log_is_unreliable_not_an_error(feed):
    feed(_n_trades(40, 15, -100.0))  # no tmp market log was created, replay is the real code
    s = _nifty_sell()
    assert s["verdict"] == "replay_unreliable"
    assert "could not read the recorded prices" in s["message"].lower()


def test_a_suggestion_changes_nothing(replay):
    import index_ai.config as cfg

    sell_before = dict(SELL_TRAIL_POINTS)
    buy_before = {i: get_instrument(i).trail_distance_points for i in INDEXES}
    out = er.run_recheck("button")
    seg = _seg(out, "NIFTY", "sell")
    assert seg["verdict"] == "suggestion" and seg["suggestion"]["distance"] == 30.0
    assert er._load_state()["segments"] == out["segments"]  # lives only in the stored result
    assert SELL_TRAIL_POINTS == sell_before
    assert {i: get_instrument(i).trail_distance_points for i in INDEXES} == buy_before
    assert cfg.ENV_PATH.read_text() == ""


# --- crypto and commodities segments (03-05) -------------------------------

NOW_IST = "2026-11-20T16:00:00+05:30"
NOW_UTC = "2026-11-20T10:30:00+00:00"
TRAIL_REASON = "point trail: 1.5 pts behind best 100 (stop 98.5, -1 pts from entry)"


def _w(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _crow(closed_at, pnl, reason=TRAIL_REASON, strategy="cpr_trend", day=None):
    return {
        "strategy": strategy,
        "asset": "BTCUSD",
        "mode": "paper",
        "day": day or closed_at[:10],
        "closed_at": closed_at,
        "pnl_usd": pnl,
        "exit_reason": reason,
    }


def _mrow(exit_time, net, reason="stop", day=None):
    return {
        "instrument": "CRUDEOILM",
        "exit_time": exit_time,
        "day": day or exit_time[:10],
        "net_rupees": net,
        "exit_reason": reason,
    }


@pytest.fixture
def journals(feed, tmp_path, monkeypatch):
    """Tmp crypto + commodity journals, a fixed live crypto rule and fixed clocks. Nothing here can
    reach the real journals, the real .env or the weekday."""
    from types import SimpleNamespace

    c, m = tmp_path / "crypto_journal.jsonl", tmp_path / "commodity_journal.jsonl"
    monkeypatch.setattr("crypto.journal.JOURNAL_PATH", c)
    monkeypatch.setattr("commodities.lanes.JOURNAL_PATH", m)
    monkeypatch.setattr(
        "crypto.config.crypto_settings", lambda: SimpleNamespace(point_trail_pct=1.6)
    )
    monkeypatch.setattr(er, "now_ist_iso", lambda: NOW_IST)
    monkeypatch.setattr(er, "_utc_now_iso", lambda: NOW_UTC)
    return c, m


def _pooled(result, seg):
    return next(s for s in result["segments"] if s["segment"] == seg)


def test_segments_crypto_commodities(journals):
    c, m = journals
    _w(
        c,
        [
            _crow("2026-11-10T08:00:00+00:00", 2.0),  # counts
            _crow("2026-11-10T09:00:00+00:00", -1.0, "manual close"),  # counts
            _crow("2026-11-11T09:00:00+00:00", 5.0, strategy="ak_roxx_pro"),  # own trail
            _crow("2026-11-11T10:00:00+00:00", 5.0, strategy="btc_daily_straddle"),  # own lane
            _crow("2026-09-25T11:59:59+00:00", 9.0),  # before the point trail existed
            _crow("2026-09-25T12:00:00+00:00", 1.0),  # first point-trail exit: counts
        ],
    )
    _w(
        m,
        [
            _mrow("2026-11-12T10:00:00+05:30", 300.0),
            _mrow("2026-11-13T10:00:00+05:30", -100.0, "square_off"),
        ],
    )
    out = er.compute_segments()
    assert out["errors"] == []
    cs = _pooled(out, "crypto:point_trail")
    assert cs["venue"] == "crypto" and cs["currency"] == "USD" and cs["distance"] == 1.6
    assert cs["distance_label"] == "1.6% trail" and cs["label"] == "Crypto (all coins)"
    assert cs["trades"] == 3 and cs["trading_days"] == 2 and cs["net"] == 2.0
    assert cs["wins"] == 2 and cs["trail_hits"] == 2
    assert cs["exit_mix"] == {"trail_stop": 2, "manual": 1}
    ms = _pooled(out, "commodities:atr_trail")
    assert ms["currency"] == "INR" and ms["trades"] == 2 and ms["net"] == 200.0
    assert ms["trail_hits"] == 1 and ms["exit_mix"] == {"trail_stop": 1, "time_exit": 1}
    assert "daily range" in ms["distance_label"]
    assert set(ms["distance"]) == set(er.COMMODITY_RULE_KEYS)


def test_segments_commodities_after_the_data_epoch(journals, feed):
    c, m = journals
    _w(m, [_mrow("2026-11-01T10:00:00+05:30", 10.0), _mrow("2026-11-03T10:00:00+05:30", 20.0)])
    feed([], epoch="2026-11-02T00:00:00+05:30")
    assert _pooled(er.compute_segments(), "commodities:atr_trail")["trades"] == 1


def test_segments_crypto_commodities_missing_and_bad_journals(journals):
    c, m = journals  # neither file exists
    out = er.compute_segments()
    assert out["errors"] == [] and len(out["segments"]) == 8
    assert _pooled(out, "crypto:point_trail")["trades"] == 0
    assert _pooled(out, "commodities:atr_trail")["trades"] == 0
    good = json.dumps(_crow("2026-11-10T08:00:00+00:00", 1.0))
    c.write_text("\n{not json\n" + good + "\n", encoding="utf-8")
    assert _pooled(er.compute_segments(), "crypto:point_trail")["trades"] == 1


def test_rule_change_restarts_window(journals, monkeypatch):
    from types import SimpleNamespace

    c, m = journals
    _w(c, [_crow("2026-11-10T08:00:00+00:00", 2.0)])
    _w(m, [_mrow("2026-11-10T10:00:00+05:30", 50.0)])
    first = er.compute_segments()
    assert first["rules"]["crypto"]["since"] == er.CRYPTO_POINT_TRAIL_SINCE
    assert first["rules"]["commodities"]["since"] is None
    assert _pooled(first, "crypto:point_trail")["trades"] == 1
    assert _pooled(first, "commodities:atr_trail")["trades"] == 1
    # unchanged rule -> same window, even a day later
    monkeypatch.setattr(er, "_utc_now_iso", lambda: "2026-11-21T10:30:00+00:00")
    again = er.compute_segments(first)
    assert again["rules"] == first["rules"]
    # the crypto point trail goes 1.6 -> 2.0: the window restarts at the run time
    monkeypatch.setattr(
        "crypto.config.crypto_settings", lambda: SimpleNamespace(point_trail_pct=2.0)
    )
    chg = er.compute_segments(first)
    assert chg["rules"]["crypto"] == {
        "rule": {"point_trail_pct": 2.0},
        "since": "2026-11-21T10:30:00+00:00",
    }
    assert _pooled(chg, "crypto:point_trail")["trades"] == 0  # the old row is no longer counted
    assert _pooled(chg, "crypto:point_trail")["distance"] == 2.0
    assert _pooled(chg, "commodities:atr_trail")["trades"] == 1  # other venue untouched
    # a row closed after the change counts on the next run, which keeps the same since
    _w(c, [_crow("2026-11-10T08:00:00+00:00", 2.0), _crow("2026-11-21T11:00:00+00:00", 3.0)])
    nxt = er.compute_segments(chg)
    assert nxt["rules"] == chg["rules"] and _pooled(nxt, "crypto:point_trail")["trades"] == 1
    # one commodity ATR constant changes -> that segment restarts at the IST run time
    monkeypatch.setattr("commodities.lanes.ATR_K_TRAIL", 0.5)
    cc = er.compute_segments(nxt)
    assert cc["rules"]["commodities"]["since"] == NOW_IST
    assert cc["rules"]["commodities"]["rule"]["ATR_K_TRAIL"] == 0.5
    assert _pooled(cc, "commodities:atr_trail")["trades"] == 0
    assert cc["rules"]["crypto"] == nxt["rules"]["crypto"]


def _pool_rows(n, days, pnl):
    return [
        _crow(
            f"2026-11-{1 + i % days:02d}T08:{i:02d}:00+00:00",
            pnl,
            day=f"2026-11-{1 + i % days:02d}",
        )
        for i in range(n)
    ]


def test_pooled_ready_not_frozen_is_no_replay_data(journals):
    c, m = journals
    _w(c, _pool_rows(40, 15, -1.0))
    _w(m, [_mrow(f"2026-11-{1 + i % 15:02d}T10:{i:02d}:00+05:30", -5.0) for i in range(40)])
    out = er.compute_segments()
    for seg in ("crypto:point_trail", "commodities:atr_trail"):
        s = _pooled(out, seg)
        assert s["state"] == "ready" and s["frozen"] is False
        assert s["verdict"] == "no_replay_data" and s["suggestion"] is None
        assert "no recorded price path" in s["message"]


def test_pooled_ready_frozen_is_working_and_below_bar_is_not_enough(journals):
    c, m = journals
    _w(c, _pool_rows(40, 15, 1.0))
    _w(
        m, [_mrow(f"2026-11-{1 + i % 14:02d}T10:{i:02d}:00+05:30", 5.0) for i in range(40)]
    )  # 14 days
    out = er.compute_segments()
    s = _pooled(out, "crypto:point_trail")
    assert s["state"] == "ready" and s["frozen"] is True and s["verdict"] == "working"
    assert _pooled(out, "commodities:atr_trail")["verdict"] == "not_enough_data"


# --- drift: baseline, 15-point rule, one plain message per event (03-05) ----

SEG = "india_NIFTY_sell"
T_BASE = "2026-11-12T16:00:00+05:30"


def test_drift_threshold_exact():
    # exactly 15 points is never drift, even where floats say 0.55 - 0.40 = 0.15000000000000002
    assert 22 / 40 - 16 / 40 > 0.15
    assert er._moved_more_than(22, 40, 16, 40) is False
    assert er._moved_more_than(23, 40, 16, 40) is True
    assert er._moved_more_than(16, 40, 23, 40) is True  # the other direction
    assert er._moved_more_than(1501, 10000, 0, 10000) is True
    assert er._moved_more_than(1500, 10000, 0, 10000) is False
    assert er._moved_more_than(1, 0, 0, 40) is False and er._moved_more_than(1, 40, 0, 0) is False


def _row(hits=16, wins=22, n=40, state="ready", distance=40.0, seg=SEG):
    return {
        "segment": seg,
        "venue": "india",
        "lane": "sell",
        "instrument": "NIFTY",
        "label": "NIFTY sell",
        "distance": distance,
        "distance_label": f"{distance:g}-point stop",
        "currency": "INR",
        "trades": n,
        "trading_days": 15,
        "state": state,
        "frozen": False,
        "window_n": n,
        "wins": wins,
        "trail_hits": hits,
        "win_rate": round(wins / n, 3),
        "trail_hit_rate": round(hits / n, 3),
        "net": 0.0,
        "exit_mix": {},
        "suggestion": None,
        "drift": False,
        "baseline": None,
        "verdict": "working",
        "message": "",
    }


@pytest.fixture
def drift(monkeypatch):
    """Controlled segment rows, a fixed clock and a captured Telegram alert. Nothing here can send
    a real message: index_ai.notify.alert is replaced and conftest clears the bot variables."""
    box: dict = {"rows": [], "clock": T_BASE}
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    monkeypatch.setattr(
        er,
        "compute_segments",
        lambda prev_state=None: {
            "segments": [dict(r) for r in box["rows"]],
            "errors": [],
            "rules": {},
        },
    )
    monkeypatch.setattr(er, "now_ist_iso", lambda: box["clock"])
    return box, alerts


def _go(box, **kw):
    box["rows"] = [_row(**kw)]
    return er.run_recheck("daily")["segments"][0]


def test_drift_below_bar(drift):
    box, alerts = drift
    s = _go(box, hits=0, wins=0, n=39, state="observing")
    s = _go(box, hits=39, wins=39, n=39, state="observing")  # a 100-point swing
    assert s["drift"] is False and s["baseline"] is None and alerts == []
    assert er._load_state().get("baselines") == {}
    # a baseline that already exists is kept, never judged, once the segment drops below the bar
    _go(box)  # ready: baseline captured
    s = _go(box, hits=40, wins=0, n=40, state="observing")
    assert s["drift"] is False and alerts == []
    assert er._load_state()["baselines"][SEG]["trail_hits"] == 16


def test_drift_baseline_capture_and_restart(drift):
    box, alerts = drift
    s = _go(box)
    base = er._load_state()["baselines"][SEG]  # a restart is just a fresh read of the stored row
    assert base == {
        "trail_hits": 16,
        "wins": 22,
        "n": 40,
        "trail_hit_rate": 0.4,
        "win_rate": 0.55,
        "distance": 40.0,
        "captured_at": T_BASE,
        "drifted": False,
    }
    assert s["drift"] is False and s["baseline"] == base and alerts == []
    box["clock"] = "2026-11-21T16:00:00+05:30"
    s = _go(box, hits=18, wins=20)  # small moves: compared with the stored baseline, which is kept
    assert s["drift"] is False and s["baseline"]["captured_at"] == T_BASE
    assert s["drift_detail"] == {
        "trail_hit_rate": {"was": 0.4, "now": 0.45},
        "win_rate": {"was": 0.55, "now": 0.5},
    }
    assert er._load_state()["baselines"][SEG]["captured_at"] == T_BASE and alerts == []


def test_drift_distance_change_rebaselines(drift):
    box, alerts = drift
    _go(box)
    box["clock"] = "2026-11-25T16:00:00+05:30"
    s = _go(box, hits=40, wins=0, distance=50.0)  # a new stop distance AND a huge swing: no alert
    base = er._load_state()["baselines"][SEG]
    assert s["drift"] is False and alerts == []
    assert base["captured_at"] == "2026-11-25T16:00:00+05:30" and base["distance"] == 50.0
    assert base["trail_hits"] == 40 and base["drifted"] is False


def test_drift_alert_once(drift):
    box, alerts = drift
    _go(box)  # 1: baseline 16/40 stops, 22/40 wins
    assert alerts == []
    s = _go(box, hits=23)  # 2: +17.5 points on the stop rate
    key = f"exit-drift:{SEG}:{T_BASE}"
    assert s["drift"] is True and [k for _, k in alerts] == [key]
    assert er._load_state()["baselines"][SEG]["drifted"] is True
    s = _go(box, hits=24)  # 3: still drifting: nothing more
    assert s["drift"] is True and len(alerts) == 1
    s = _go(box, hits=18)  # 4: back within 15 points
    assert s["drift"] is False and len(alerts) == 1
    assert er._load_state()["baselines"][SEG]["drifted"] is False
    _go(box, hits=23)  # 5: drifted again: one more
    assert len(alerts) == 2 and alerts[1][1] == key


def test_drift_on_win_rate_alone_alerts(drift):
    box, alerts = drift
    _go(box)
    s = _go(box, wins=15)  # 55% -> 37.5%, the stop rate unchanged
    assert s["drift"] is True and len(alerts) == 1
    s = _go(box, wins=16)  # exactly 15 points lower than 55%: not drift
    assert s["drift"] is False


def test_drift_alert_text_plain(drift):
    box, alerts = drift
    _go(box)
    _go(box, hits=24, wins=18)  # stop closed 60% (was 40%), 45% made money (was 55%)
    text, key = alerts[0]
    assert "NIFTY sell" in text and "40-point stop" in text and "12 Nov" in text
    assert "the stop closed 60% (was 40%)" in text and "45% made money (was 55%)" in text
    assert "Nothing was changed" in text
    for word in ("baseline", "drift", "ladder", " pp"):
        assert word not in text.lower()
    assert key == f"exit-drift:{SEG}:{T_BASE}"


def test_drift_state_is_stored_before_the_alert_and_a_failing_alert_is_harmless(drift, monkeypatch):
    box, alerts = drift
    _go(box)
    seen = []

    def boom(text, *, key, **k):
        seen.append(er._load_state()["baselines"][SEG]["drifted"])  # already stored when we send
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("index_ai.notify.alert", boom)
    s = _go(box, hits=23)  # must not raise out of run_recheck
    assert s["drift"] is True and seen == [True]
    assert er._load_state()["baselines"][SEG]["drifted"] is True
    _go(box, hits=23)  # and it is not retried every run
    assert seen == [True]


def test_drift_through_real_india_rows(feed, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    feed(_n_trades(40, 15, -100.0))  # ready, losing, no stop exits, no wins
    first = er.run_recheck("daily")
    assert _seg(first, "NIFTY", "sell")["baseline"]["trail_hits"] == 0 and alerts == []
    note = "Index trail: 1 crossed the stop 2 (40 pts behind best 3; 4 pts from entry)."
    feed(_n_trades(40, 15, -100.0), notes={str(i): note for i in range(7)})  # 7 of 40 = 17.5 points
    second = er.run_recheck("daily")
    s = _seg(second, "NIFTY", "sell")
    assert s["drift"] is True and s["trail_hits"] == 7
    assert len(alerts) == 1 and alerts[0][1].startswith("exit-drift:india_NIFTY_sell:")
    er.run_recheck("daily")
    assert len(alerts) == 1  # a later run while still drifting sends nothing


# --- the daily run: a contained step of daily_ops.run_eod (03-05) -----------


def _eod(tmp_path, monkeypatch):
    from index_ai import daily_ops

    monkeypatch.setattr(daily_ops, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(daily_ops, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(daily_ops, "eod_due", lambda: True)
    return daily_ops


def test_run_eod_hook(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        er, "run_recheck", lambda trigger="button": calls.append(trigger) or {"ran": True}
    )
    report = _eod(tmp_path, monkeypatch).run_eod()
    assert calls == ["daily"]
    assert report["exit_recheck"] == {"ran": True}


def test_run_eod_hook_failure_is_contained(tmp_path, monkeypatch):
    def boom(trigger="button"):
        raise RuntimeError("the re-check broke")

    monkeypatch.setattr(er, "run_recheck", boom)
    daily_ops = _eod(tmp_path, monkeypatch)
    report = daily_ops.run_eod()
    assert report["exit_recheck"] == {"error": "the re-check broke"}
    for key in ("brain", "spreads", "strategy_learning"):
        assert key in report
    assert (
        json.loads((tmp_path / "s.json").read_text())["eod_date"] == report["date"]
    )  # day still stamped
