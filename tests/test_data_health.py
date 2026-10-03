"""Data health panel (UIUX-02): read-only ages of the data the bot trades on.

Every time here is a fixed IST datetime -- nothing depends on today's date --
and every file is a tmp_path file (conftest points market_log.DB_PATH and
spread_calib.SAMPLES_PATH at tmp already).
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from index_ai import data_health, market_clock, tick_feed

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
    for block in ("ticks", "feed"):
        assert body[block]["status"] in data_health.STATUSES
    assert "last_error" not in r.text
    assert "wss://" not in r.text
    assert client.post("/api/data-health").status_code == 405


def app_for_tests():
    from index_ai.server import app

    return app


@pytest.fixture(autouse=True)
def _quiet_feed(monkeypatch):
    """Whatever the real feed state is, tests start from a fresh, switched-off one."""
    monkeypatch.setattr(tick_feed, "_state", tick_feed.FeedState())
    monkeypatch.delenv("ENABLE_TICK_FEED", raising=False)
