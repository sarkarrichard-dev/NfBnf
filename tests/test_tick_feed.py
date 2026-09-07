import struct

from index_ai import tick_feed
from index_ai.tick_feed import (
    CODE_DISCONNECT,
    CODE_OI,
    CODE_QUOTE,
    CODE_TICKER,
    HEADER,
    parse_packet,
)


def _ticker(sec=13, ltp=24175.65):
    return struct.pack("<BHBI", CODE_TICKER, 16, 0, sec) + struct.pack("<fI", ltp, 1756400000)


def _quote(sec=13):
    payload = struct.pack("<fHIfIIIffff", 24180.5, 50, 1756400001, 24170.0,
                          123456, 700, 800, 24100.0, 24050.0, 24250.0, 24000.0)
    return struct.pack("<BHBI", CODE_QUOTE, HEADER + len(payload), 0, sec) + payload


def test_ticker_packet_decodes():
    p = parse_packet(_ticker())[0]
    assert p["type"] == "ticker" and p["ltp"] == 24175.65 and p["security_id"] == 13


def test_quote_packet_decodes_all_fields():
    p = parse_packet(_quote())[0]
    assert p["ltp"] == 24180.5 and p["volume"] == 123456
    assert p["high"] == 24250.0 and p["low"] == 24000.0
    # per the openalgo layout: offset 18 is SELL qty, offset 22 is BUY qty
    assert p["total_sell_quantity"] == 700 and p["total_buy_quantity"] == 800


def test_multiple_packets_in_one_frame():
    got = parse_packet(_ticker(13) + _quote(25))
    assert [g["security_id"] for g in got] == [13, 25]


def test_malformed_input_never_raises():
    assert parse_packet(b"") == []
    assert parse_packet(b"\x02\xff") == []
    # length that overruns the buffer must stop cleanly, not slice garbage
    assert parse_packet(struct.pack("<BHBI", CODE_TICKER, 9999, 0, 13)) == []
    # truncated payload for the declared code
    short = struct.pack("<BHBI", CODE_QUOTE, HEADER + 4, 0, 13) + b"\x00\x00\x00\x00"
    assert parse_packet(short) == []


def test_oi_and_disconnect_codes():
    oi = struct.pack("<BHBI", CODE_OI, 12, 0, 13) + struct.pack("<I", 202780)
    assert parse_packet(oi)[0]["oi"] == 202780
    d = parse_packet(struct.pack("<BHBI", CODE_DISCONNECT, 8, 0, 13))[0]
    assert d["type"] == "disconnect"


def test_feed_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_TICK_FEED", raising=False)
    assert tick_feed.enabled() is False
    monkeypatch.setenv("ENABLE_TICK_FEED", "true")
    assert tick_feed.enabled() is True


def test_status_reports_stall_detection():
    s = tick_feed.status()
    assert set(s) >= {"enabled", "connected", "ticks", "stalled", "reconnects"}
    assert s["stalled"] is False


def test_feed_instruments_default_all_three(monkeypatch):
    monkeypatch.delenv("TICK_FEED_INSTRUMENTS", raising=False)
    assert tick_feed.feed_instruments() == ["NIFTY", "BANKNIFTY", "SENSEX"]
