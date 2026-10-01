import asyncio
import struct

import websockets
import websockets.exceptions

from _fake_brokers import FakeDhanFeed, load_feed_frames
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
    payload = struct.pack(
        "<fHIfIIIffff",
        24180.5,
        50,
        1756400001,
        24170.0,
        123456,
        700,
        800,
        24100.0,
        24050.0,
        24250.0,
        24000.0,
    )
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


# --- run_feed fault injection on real recorded frames (plan 02-06, ORD-03) --
#
# Replays tests/fixtures/broker_traffic/dhan_feed.jsonl (real Dhan tick-feed
# frames, captured 2026-10-01 during NSE hours, D-03) through the real
# run_feed() with websockets.connect faked out (D-04).


def _setup(monkeypatch):
    """Fresh FeedState + feed_instruments pinned to the three instruments the
    real capture subscribed to + a record_tick_batch recorder that appends
    ("flush", rows) to a shared events list (also used by FakeDhanFeed for
    "connect" events, so a test can compare ordering)."""
    state = tick_feed.FeedState()
    monkeypatch.setattr(tick_feed, "_state", state)
    monkeypatch.setattr(tick_feed, "feed_instruments", lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])

    events: list[tuple] = []

    def _recorder(rows, sec_map):
        rows = list(rows)
        events.append(("flush", rows))
        return len(rows)

    import index_ai.market_log as market_log

    monkeypatch.setattr(market_log, "record_tick_batch", _recorder)
    return events, state


def _frames(n: int) -> list[bytes]:
    """`n` real recorded frames, cycling the recording if it is short."""
    pool = load_feed_frames()
    assert pool, (
        "tests/fixtures/broker_traffic/dhan_feed.jsonl has no frames — run the feed capture"
    )
    return [pool[i % len(pool)] for i in range(n)]


def _packet_count(frames: list[bytes]) -> int:
    return sum(len(parse_packet(f)) for f in frames)


def test_hard_drop_flushes_received_ticks_before_reconnecting(monkeypatch):
    events, state = _setup(monkeypatch)
    frames = _frames(30)
    assert _packet_count(frames) >= 25

    stop = asyncio.Event()
    sessions = [
        [*frames, websockets.exceptions.ConnectionClosedError(None, None)],
        [stop.set],
    ]
    fake = FakeDhanFeed(sessions, events=events)
    monkeypatch.setattr(websockets, "connect", fake.connect)

    asyncio.run(tick_feed.run_feed("tok", "cid", stop=stop))

    flush_idx = next(i for i, e in enumerate(events) if e[0] == "flush")
    connect2_idx = next(i for i, e in enumerate(events) if e == ("connect", 1))
    assert flush_idx < connect2_idx, "connection 1's ticks must flush before connection 2 opens"

    total_written = sum(len(rows) for name, rows in events if name == "flush")
    assert total_written == _packet_count(frames)
    assert state.reconnects == 1


def test_shutdown_during_outage_does_not_lose_received_ticks(monkeypatch):
    events, state = _setup(monkeypatch)
    frames = _frames(30)

    stop = asyncio.Event()
    sessions = [
        [*frames, websockets.exceptions.ConnectionClosedError(None, None)],
    ]
    fake = FakeDhanFeed(sessions, events=events)
    monkeypatch.setattr(websockets, "connect", fake.connect)

    async def _main():
        task = asyncio.create_task(tick_feed.run_feed("tok", "cid", stop=stop))
        await asyncio.sleep(0.05)  # let connection 1 crash and enter its backoff wait
        stop.set()  # shut down while the feed is down, before any reconnect
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_main())

    total_written = sum(len(rows) for name, rows in events if name == "flush")
    assert total_written == _packet_count(frames)


def test_normal_stop_writes_tail_once_with_no_drop(monkeypatch):
    events, state = _setup(monkeypatch)
    frames = _frames(10)

    stop = asyncio.Event()
    sessions = [[*frames, stop.set]]
    fake = FakeDhanFeed(sessions, events=events)
    monkeypatch.setattr(websockets, "connect", fake.connect)

    asyncio.run(tick_feed.run_feed("tok", "cid", stop=stop))

    total_written = sum(len(rows) for name, rows in events if name == "flush")
    assert total_written == _packet_count(frames)
    assert state.reconnects == 0


# (Task 2's tests — server-disconnect, on_tick-failure and busy-scan-cycle —
# are added by the next commit; tick_feed.py's disconnect/on_tick_errors
# behavior lands with them.)
