"""Data health panel (UIUX-02): read-only ages of the data the bot trades on.

Every time here is a fixed IST datetime -- nothing depends on today's date --
and every file is a tmp_path file (conftest points market_log.DB_PATH and
spread_calib.SAMPLES_PATH at tmp already).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from index_ai import data_health, market_clock, market_log, tick_feed
from index_ai.market_context import spread_calib

IST = market_clock.IST
OPEN = datetime(2026, 9, 30, 11, 0, tzinfo=IST)  # Wednesday
CLOSED = datetime(2026, 10, 3, 11, 0, tzinfo=IST)  # Saturday
HOLIDAY = datetime(2026, 10, 2, 11, 0, tzinfo=IST)  # Gandhi Jayanti
BELL = datetime(2026, 9, 30, 9, 16, tzinfo=IST)
SQUARE = datetime(2026, 9, 30, 15, 20, tzinfo=IST)


def test_classify_thresholds():
    assert data_health.THRESHOLDS == {
        "ticks": (60, 300),
        "chain": (300, 600),
        "spread": (600, 1800),
    }
    for amber, red in data_health.THRESHOLDS.values():
        assert data_health.classify(amber - 1, True, amber, red) == "ok"
        assert data_health.classify(amber, True, amber, red) == "slow"
        assert data_health.classify(red - 1, True, amber, red) == "slow"
        assert data_health.classify(red, True, amber, red) == "stale"
        assert data_health.classify(10**6, False, amber, red) == "closed"
        assert data_health.classify(None, True, amber, red) == "none"
        assert data_health.classify(red, True, amber, red, in_grace=True) == "slow"


def test_feed_status_words():
    assert data_health.feed_status(False, True, False, True) == "off"
    assert data_health.feed_status(True, True, False, False) == "closed"
    assert data_health.feed_status(True, True, False, True) == "ok"
    assert data_health.feed_status(True, True, True, True) == "slow"
    assert data_health.feed_status(True, False, False, True) == "slow"


def test_tick_lines_follow_the_feed(monkeypatch):
    monkeypatch.delenv("ENABLE_TICK_FEED", raising=False)
    monkeypatch.setattr(tick_feed, "_state", tick_feed.FeedState())
    d = data_health.collect(now=OPEN)
    assert (d["ticks"]["status"], d["feed"]["status"]) == ("off", "off")

    monkeypatch.setenv("ENABLE_TICK_FEED", "true")
    monkeypatch.setattr(
        tick_feed, "_state", tick_feed.FeedState(connected=True, last_tick_at=time.monotonic())
    )
    d = data_health.collect(now=OPEN)
    assert (d["ticks"]["status"], d["feed"]["status"]) == ("ok", "ok")

    down = tick_feed.FeedState(connected=False, last_tick_at=time.monotonic() - 600)
    monkeypatch.setattr(tick_feed, "_state", down)
    d = data_health.collect(now=OPEN)
    assert (d["ticks"]["status"], d["feed"]["status"]) == ("stale", "slow")
    for quiet in (CLOSED, HOLIDAY):
        d = data_health.collect(now=quiet)
        assert (d["ticks"]["status"], d["feed"]["status"]) == ("closed", "closed")
    d = data_health.collect(now=BELL)
    assert (d["ticks"]["status"], d["feed"]["status"]) == ("slow", "slow")


def test_data_health_endpoint(monkeypatch):
    monkeypatch.setattr(market_clock, "now_ist", lambda: OPEN)
    client = TestClient(app_for_tests())  # no with-block: never start background loops
    r = client.get("/api/data-health")
    assert r.status_code == 200
    body = r.json()
    for key in ("as_of", "as_of_display", "market_open", "square_off_window", "ticks", "feed"):
        assert key in body
    assert body["market_open"] is True
    for block in ("ticks", "feed", "chain", "spread"):
        assert body[block]["status"] in data_health.STATUSES
    for block in ("chain", "spread"):
        for key in ("age_seconds", "last_seen", "by_index"):
            assert key in body[block]
        assert body[block]["status"] == "none"  # conftest's tmp paths are empty
    assert not market_log.DB_PATH.exists()  # a read never creates the database
    assert "last_error" not in r.text
    assert "wss://" not in r.text
    assert client.post("/api/data-health").status_code == 405


# --- chain and spread lines ----------------------------------------------------

KEYS = ("NIFTY", "BANKNIFTY", "SENSEX")


@pytest.fixture(autouse=True)
def _three_indices(monkeypatch):
    monkeypatch.setattr("index_ai.instruments.configured_index_keys", lambda: KEYS)


def _chain_db(path, rows):
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE chain (id INTEGER PRIMARY KEY, ts TEXT, session TEXT, instrument TEXT)"
    )
    db.execute("CREATE INDEX idx_chain_session ON chain(session, instrument, ts)")
    db.executemany("INSERT INTO chain (ts, session, instrument) VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def _use_chain(tmp_path, monkeypatch, rows):
    path = tmp_path / "chain_ro.sqlite"
    _chain_db(path, rows)
    monkeypatch.setattr(market_log, "DB_PATH", path)
    return path


def _spread_file(tmp_path, monkeypatch, lines):
    path = tmp_path / "spreads.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(spread_calib, "SAMPLES_PATH", path)
    return path


def _sample(instrument, at):
    return json.dumps({"at": at, "instrument": instrument, "strike": 25000.0, "half_spread": 0.5})


def test_chain_reads_newest_rows_read_only(tmp_path, monkeypatch):
    path = _use_chain(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-30T09:20:00+05:30", "2026-09-30", "NIFTY"),
            ("2026-09-30T10:59:00+05:30", "2026-09-30", "NIFTY"),
            ("2026-09-30T10:58:00+05:30", "2026-09-30", "BANKNIFTY"),
            ("2026-09-29T15:09:00+05:30", "2026-09-29", "SENSEX"),
        ],
    )
    before = (sorted(p.name for p in tmp_path.iterdir()), path.read_bytes())
    assert data_health.chain_last_seen(KEYS) == {
        "NIFTY": "2026-09-30T10:59:00+05:30",
        "BANKNIFTY": "2026-09-30T10:58:00+05:30",
        "SENSEX": "2026-09-29T15:09:00+05:30",  # found by stepping back one session
    }
    assert (sorted(p.name for p in tmp_path.iterdir()), path.read_bytes()) == before

    chain = data_health.collect(now=OPEN)["chain"]
    assert chain["by_index"] == {"NIFTY": 60, "BANKNIFTY": 120, "SENSEX": 71460}
    assert chain["age_seconds"] == 71460
    assert chain["last_at"] == "2026-09-29T15:09:00+05:30"
    assert chain["last_seen"] == "29 Sep 2026, 3:09:00 PM IST"
    assert chain["status"] == "stale"


def test_chain_missing_database_creates_nothing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setattr(market_log, "DB_PATH", missing)
    assert data_health.chain_last_seen(KEYS) == {k: None for k in KEYS}
    assert not missing.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_spread_age_from_the_file_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(data_health, "SPREAD_TAIL_BYTES", 4096)
    day = "2026-09-30T"
    lines = [_sample("SENSEX", f"{day}09:30:00+05:30")] * 5
    lines += [_sample("NIFTY" if i % 2 else "BANKNIFTY", f"{day}10:20:00+05:30") for i in range(70)]
    lines += [
        "this line is not json",
        _sample("NIFTY", f"{day}10:55:00+05:30"),
        _sample("BANKNIFTY", f"{day}10:57:00+05:30"),
    ]
    path = _spread_file(tmp_path, monkeypatch, lines)
    assert path.stat().st_size > 4096
    assert data_health.spread_last_seen(KEYS) == {
        "NIFTY": f"{day}10:55:00+05:30",
        "BANKNIFTY": f"{day}10:57:00+05:30",
        "SENSEX": None,  # its lines are older than the tail
    }
    monkeypatch.setattr(spread_calib, "SAMPLES_PATH", tmp_path / "nope.jsonl")
    assert data_health.spread_last_seen(KEYS) == {k: None for k in KEYS}


def test_square_off_pauses_the_chain_line(tmp_path, monkeypatch):
    _use_chain(
        tmp_path, monkeypatch, [("2026-09-30T15:09:00+05:30", "2026-09-30", k) for k in KEYS]
    )
    _spread_file(tmp_path, monkeypatch, [_sample(k, "2026-09-30T15:19:30+05:30") for k in KEYS])
    d = data_health.collect(now=SQUARE)
    assert d["market_open"] is True and d["square_off_window"] is True
    assert d["chain"]["status"] == "closed"
    assert d["spread"]["status"] == "ok"


def test_open_bell_is_graced(tmp_path, monkeypatch):
    _use_chain(
        tmp_path, monkeypatch, [("2026-09-29T15:09:00+05:30", "2026-09-29", k) for k in KEYS]
    )
    assert data_health.collect(now=BELL)["chain"]["status"] == "slow"  # not "stale"


def test_closed_market_is_never_amber_or_red(tmp_path, monkeypatch):
    _use_chain(
        tmp_path, monkeypatch, [("2026-09-25T10:00:00+05:30", "2026-09-25", k) for k in KEYS]
    )
    _spread_file(tmp_path, monkeypatch, [_sample(k, "2026-09-25T10:00:00+05:30") for k in KEYS])
    monkeypatch.setenv("ENABLE_TICK_FEED", "true")
    monkeypatch.setattr(
        tick_feed,
        "_state",
        tick_feed.FeedState(connected=False, last_tick_at=time.monotonic() - 600),
    )
    for quiet in (CLOSED, HOLIDAY):
        d = data_health.collect(now=quiet)
        assert d["market_open"] is False
        for block in ("ticks", "feed", "chain", "spread"):
            assert d[block]["status"] in {"closed", "off", "none"}, (quiet, block)


def app_for_tests():
    from index_ai.server import app

    return app


@pytest.fixture(autouse=True)
def _quiet_feed(monkeypatch):
    """Whatever the real feed state is, tests start from a fresh, switched-off one."""
    monkeypatch.setattr(tick_feed, "_state", tick_feed.FeedState())
    monkeypatch.delenv("ENABLE_TICK_FEED", raising=False)
