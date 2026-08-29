import json
from pathlib import Path

import pytest

from index_ai import market_log


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(market_log, "DB_PATH", tmp_path / "m.sqlite")
    monkeypatch.setenv("ENABLE_MARKET_LOG", "true")
    return tmp_path


def test_records_observation_and_reads_it_back(db):
    market_log.record_observation(
        "NIFTY", spot=24175.65, atm_iv=10.3, pcr=0.92, max_pain=24200,
        near_half_spread=0.2, wing_half_spread=0.6, vix=10.66,
        regime="TREND", signal_direction="LONG", expiry="2026-09-01",
    )
    rows = market_log.observations(instrument="NIFTY")
    assert len(rows) == 1
    assert rows[0]["spot"] == 24175.65 and rows[0]["regime"] == "TREND"
    # unknown kwargs land in extra rather than being dropped
    assert json.loads(rows[0]["extra"])["expiry"] == "2026-09-01"


def test_records_refusals_which_is_the_point(db):
    market_log.record_decision("NIFTY", "sell", "entry", traded=True,
                               reason="breakout", win_probability=0.6)
    for _ in range(3):
        market_log.record_decision("BANKNIFTY", "sell", "skip", traded=False,
                                   reason="NOT_VIABLE: cannot cover floor")
    s = market_log.stats()
    assert s["decisions"] == 4 and s["traded"] == 1 and s["skipped"] == 3
    top = market_log.skip_reasons()
    assert top[0]["n"] == 3 and "NOT_VIABLE" in top[0]["reason"]
    assert top[0]["instrument"] == "BANKNIFTY"


def test_disabled_is_a_silent_noop(db, monkeypatch):
    monkeypatch.setenv("ENABLE_MARKET_LOG", "false")
    market_log.record_observation("NIFTY", spot=1.0)
    market_log.record_decision("NIFTY", "sell", "entry", traded=True)
    assert market_log.stats()["observations"] == 0


def test_bad_values_never_raise(db):
    market_log.record_observation("NIFTY", spot="not-a-number", pcr=None)
    rows = market_log.observations()
    assert len(rows) == 1 and rows[0]["spot"] is None


def test_filters_by_session_and_instrument(db):
    market_log.record_observation("NIFTY", spot=1.0)
    market_log.record_observation("SENSEX", spot=2.0)
    assert len(market_log.observations()) == 2
    assert len(market_log.observations(instrument="SENSEX")) == 1
    assert market_log.observations(session="1999-01-01") == []


def test_prune_drops_old_sessions(db):
    market_log.record_observation("NIFTY", spot=1.0)
    with market_log.connect() as conn:
        conn.execute("UPDATE observations SET session='2000-01-01'")
    assert market_log.prune(days=1) >= 1
    assert market_log.observations() == []


def test_stats_is_honest_about_resolution(db):
    # with no ticks collected, stats must not imply tick resolution exists
    r = market_log.stats()["resolution"]
    assert "scan cycle" in r and "ENABLE_TICK_FEED" in r


def test_paper_lanes_default_on(monkeypatch):
    monkeypatch.delenv("ENABLE_OPTIONS_CPR_PAPER", raising=False)
    monkeypatch.delenv("ENABLE_FUTURES_PAPER", raising=False)
    from index_ai.strategies.futures.paper import enabled as fut
    from index_ai.strategies.options_cpr.paper import enabled as opt

    assert opt() is True and fut() is True
    monkeypatch.setenv("ENABLE_OPTIONS_CPR_PAPER", "false")
    assert opt() is False


def test_db_lives_apart_from_the_trade_journal():
    from index_ai.config import DB_PATH as JOURNAL_DB

    assert Path(market_log.DB_PATH).name != Path(JOURNAL_DB).name


def test_tick_batch_persists_and_maps_instrument(db):
    from index_ai.tick_feed import parse_packet
    import struct

    payload = struct.pack("<fHIfIIIffff", 24180.5, 50, 1756400001, 24170.0,
                          123456, 700, 800, 24100.0, 24050.0, 24250.0, 24000.0)
    frame = struct.pack("<BHBI", 4, 8 + len(payload), 0, 13) + payload
    packets = parse_packet(frame)
    n = market_log.record_tick_batch(packets, {13: "NIFTY"})
    assert n == 1
    rows = market_log.ticks(instrument="NIFTY")
    assert len(rows) == 1
    assert rows[0]["ltp"] == 24180.5 and rows[0]["volume"] == 123456
    assert rows[0]["kind"] == "quote" and rows[0]["security_id"] == 13
    s = market_log.stats()
    assert s["ticks"] == 1 and "exchange ticks" in s["resolution"]


def test_tick_batch_is_noop_when_logging_disabled(db, monkeypatch):
    monkeypatch.setenv("ENABLE_MARKET_LOG", "false")
    assert market_log.record_tick_batch([{"security_id": 1, "type": "ticker", "ltp": 1.0}]) == 0


def test_tick_batch_skips_packets_without_a_security_id(db):
    assert market_log.record_tick_batch([{"type": "ticker", "ltp": 1.0}]) == 0
