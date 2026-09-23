from datetime import datetime, timedelta

import pytest

from index_ai import market_log
from index_ai.strategies import oi_signals

SESSION = "2026-09-24"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(market_log, "DB_PATH", tmp_path / "m.sqlite")
    monkeypatch.setenv("ENABLE_MARKET_LOG", "true")


def _insert(ts, spot, oi_fn, expiry="2026-09-29"):
    rows = [
        (ts, SESSION, "NIFTY", expiry, spot, float(k), side, oi_fn(k, side))
        for k in range(23000, 24001, 50)
        for side in ("CE", "PE")
    ]
    with market_log.connect() as con:
        con.executemany(
            "INSERT INTO chain (ts, session, instrument, expiry, spot, strike, opt_type, oi) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def _rising_day(n=40):
    """Spot climbs 5 pts every 90s; walls and put writing shift up with it."""
    start = datetime.fromisoformat(f"{SESSION}T09:30:00+05:30")
    for i in range(n):
        spot = 23500 + 5 * i
        res, sup = 23700 + 50 * (i // 10), 23300 + 50 * (i // 10)

        def oi(k, side, i=i, res=res, sup=sup):
            if side == "CE":
                return 9e6 if k == res else 1e6
            return 9e6 + 1e5 * i if k == sup else 1e6 + 2e4 * i  # puts being written

        _insert((start + timedelta(seconds=90 * i)).isoformat(), spot, oi)


def test_latest_reads_a_rising_chain_as_bullish(db):
    _rising_day()
    sig = oi_signals.latest("NIFTY", SESSION)
    assert sig["bias"] == "BULLISH"
    assert sig["votes"]["walls"] == 1 and sig["votes"]["writing"] == 1
    assert "support 23300->23450" in sig["why"]["walls"]


def test_grade_scores_calls_against_the_real_move_30_minutes_later(db):
    _rising_day()
    g = oi_signals.grade("NIFTY", [SESSION])
    assert g["bias"]["calls"] > 0 and g["bias"]["hit_rate"] == 1.0
    assert g["bias"]["avg_points"] == pytest.approx(100.0)  # 20 snapshots x 5 pts
    assert g["max_pain"]["calls"] == 0  # not expiry day


def test_no_view_with_too_little_data(db):
    assert oi_signals.latest("NIFTY", SESSION) is None
    assert oi_signals.grade("NIFTY", [SESSION])["bias"]["calls"] == 0


def test_max_pain_votes_only_on_expiry_afternoon(db):
    start = datetime.fromisoformat(f"{SESSION}T13:00:00+05:30")
    heavy = lambda k, side: 5e6 if k == 23800 else 1e5  # noqa: E731  max pain ~23800
    for i in range(2):
        _insert((start + timedelta(minutes=i)).isoformat(), 23500, heavy, expiry=SESSION)
    sig = oi_signals.latest("NIFTY", SESSION)
    assert sig["votes"]["max_pain"] == 1 and "max pain 23800" in sig["why"]["max_pain"]


def test_api_reports_record_per_index(db):
    from fastapi.testclient import TestClient

    from index_ai.server import app

    _rising_day()
    body = TestClient(app).get("/api/oi-signals").json()
    assert body["sessions_recorded"] == 1
    assert body["indices"]["NIFTY"]["record"]["bias"]["hit_rate"] == 1.0
