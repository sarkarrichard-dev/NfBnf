"""
Dhan live market feed — real exchange ticks over websocket.

The REST client polls, so ``market_log`` records at scan cadence (~90s). This is
the actual tick stream: ``wss://api-feed.dhan.co``, binary packets, one message
per trade/quote update.

Packet layout is taken from the vendored openalgo reference
(``reference/openalgo/broker/dhan/streaming/dhan_websocket.py``) rather than
guessed — an 8-byte header (response code, length, exchange segment, security id)
followed by a little-endian payload whose shape depends on the code:

    2  ticker      LTP + last-trade-time
    4  quote       LTP, LTQ, LTT, ATP, volume, buy/sell qty, OHLC
    5  oi          open interest
    6  prev close
    8  full        quote + OI + 5-level depth
    50 disconnect

Ticks are batched and written to ``market_log`` on a timer rather than per
message: a liquid index option can print hundreds of ticks a second and a SQLite
INSERT per tick would make the writer the bottleneck.

Off by default (``ENABLE_TICK_FEED``). It is an always-on socket holding a broker
session, so it should be a deliberate choice, and everything else works without it.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

FEED_URL = "wss://api-feed.dhan.co"
SUBSCRIBE_TICKER, SUBSCRIBE_QUOTE, SUBSCRIBE_FULL, DISCONNECT = 15, 17, 21, 12
MAX_BATCH = 100
HEADER = 8

CODE_TICKER, CODE_QUOTE, CODE_OI, CODE_PREV_CLOSE, CODE_FULL, CODE_DISCONNECT = 2, 4, 5, 6, 8, 50

# A stream that is TCP-alive but data-dead is the failure mode that matters:
# the socket looks fine and no ticks arrive. Reconnect on silence, not on error.
STALL_SECONDS = 90.0
FLUSH_SECONDS = 5.0
MAX_BACKOFF = 60.0


def enabled() -> bool:
    return os.getenv("ENABLE_TICK_FEED", "false").strip().lower() in {"1", "true", "yes", "on"}


def feed_instruments() -> list[str]:
    raw = os.getenv("TICK_FEED_INSTRUMENTS", "NIFTY,BANKNIFTY,SENSEX")
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def parse_packet(data: bytes) -> list[dict[str, Any]]:
    """Split a binary frame into decoded packets. Never raises on malformed input."""
    out: list[dict[str, Any]] = []
    offset = 0
    while offset + HEADER <= len(data):
        try:
            code = struct.unpack_from("<B", data, offset)[0]
            length = struct.unpack_from("<H", data, offset + 1)[0]
            segment = struct.unpack_from("<B", data, offset + 3)[0]
            sec_id = struct.unpack_from("<I", data, offset + 4)[0]
        except struct.error:
            break
        if length <= 0 or offset + length > len(data):
            break
        payload = data[offset + HEADER : offset + length]
        pkt = _decode(code, payload, segment, sec_id)
        if pkt:
            out.append(pkt)
        offset += length
    return out


def _decode(code: int, payload: bytes, segment: int, sec_id: int) -> dict[str, Any] | None:
    base = {"exchange_segment": segment, "security_id": sec_id}
    try:
        if code == CODE_TICKER and len(payload) >= 8:
            return {**base, "type": "ticker",
                    "ltp": round(struct.unpack_from("<f", payload, 0)[0], 2),
                    "ltt": struct.unpack_from("<I", payload, 4)[0]}
        if code in (CODE_QUOTE, CODE_FULL) and len(payload) >= 42:
            q = {**base, "type": "quote" if code == CODE_QUOTE else "full",
                 "ltp": round(struct.unpack_from("<f", payload, 0)[0], 2),
                 "ltq": struct.unpack_from("<H", payload, 4)[0],
                 "ltt": struct.unpack_from("<I", payload, 6)[0],
                 "atp": round(struct.unpack_from("<f", payload, 10)[0], 2),
                 "volume": struct.unpack_from("<I", payload, 14)[0],
                 "total_sell_quantity": struct.unpack_from("<I", payload, 18)[0],
                 "total_buy_quantity": struct.unpack_from("<I", payload, 22)[0],
                 "open": round(struct.unpack_from("<f", payload, 26)[0], 2),
                 "close": round(struct.unpack_from("<f", payload, 30)[0], 2),
                 "high": round(struct.unpack_from("<f", payload, 34)[0], 2),
                 "low": round(struct.unpack_from("<f", payload, 38)[0], 2)}
            if code == CODE_FULL and len(payload) >= 30:
                q["oi"] = struct.unpack_from("<I", payload, 26)[0]
            return q
        if code == CODE_OI and len(payload) >= 4:
            return {**base, "type": "oi", "oi": struct.unpack_from("<I", payload, 0)[0]}
        if code == CODE_PREV_CLOSE and len(payload) >= 8:
            return {**base, "type": "prev_close",
                    "prev_close": round(struct.unpack_from("<f", payload, 0)[0], 2),
                    "prev_oi": struct.unpack_from("<I", payload, 4)[0]}
        if code == CODE_DISCONNECT:
            return {**base, "type": "disconnect"}
    except struct.error:
        return None
    return None


@dataclass
class FeedState:
    connected: bool = False
    ticks: int = 0
    packets: int = 0
    flushed: int = 0
    reconnects: int = 0
    last_tick_at: float = 0.0
    last_error: str | None = None
    subscribed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        age = (time.monotonic() - self.last_tick_at) if self.last_tick_at else None
        return {
            "enabled": enabled(), "connected": self.connected,
            "ticks": self.ticks, "packets": self.packets, "flushed_to_db": self.flushed,
            "reconnects": self.reconnects,
            "seconds_since_last_tick": round(age, 1) if age is not None else None,
            "stalled": bool(age is not None and age > STALL_SECONDS),
            "last_error": self.last_error, "subscribed": self.subscribed,
        }


_state = FeedState()


def status() -> dict[str, Any]:
    return _state.as_dict()


def _subscription_list() -> list[dict[str, str]]:
    from index_ai.instruments import get_instrument

    out: list[dict[str, str]] = []
    for key in feed_instruments():
        try:
            inst = get_instrument(key)
            if inst.underlying_security_id is None:
                continue
            out.append({"ExchangeSegment": inst.underlying_segment,
                        "SecurityId": str(inst.underlying_security_id)})
        except Exception:
            continue
    return out


async def _flush(buffer: list[dict[str, Any]], sec_map: dict[int, str]) -> int:
    """Persist a batch of ticks. Batched because a per-tick INSERT would make the
    writer the bottleneck on a liquid instrument."""
    if not buffer:
        return 0
    rows, buffer[:] = list(buffer), []
    try:
        from index_ai.market_log import record_tick_batch

        return await asyncio.to_thread(record_tick_batch, rows, sec_map)
    except Exception as exc:
        _state.last_error = f"flush failed: {exc}"[:200]
        return 0


async def run_feed(
    access_token: str,
    client_id: str,
    *,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Connect, subscribe, decode and persist until ``stop`` is set.

    Reconnects with backoff on both errors and *silence* — a socket that stays
    open while data stops is the failure this guards against.
    """
    import websockets

    from index_ai.instruments import get_instrument

    sec_map: dict[int, str] = {}
    for key in feed_instruments():
        try:
            sid = get_instrument(key).underlying_security_id
            if sid is not None:
                sec_map[int(sid)] = key
        except Exception:
            continue

    subs = _subscription_list()
    _state.subscribed = [s["SecurityId"] for s in subs]
    if not subs:
        _state.last_error = "no configured instruments to subscribe"
        return

    url = f"{FEED_URL}?{urlencode({'version': '2', 'token': access_token, 'clientId': client_id, 'authType': '2'})}"
    backoff = 1.0
    buffer: list[dict[str, Any]] = []

    while not (stop and stop.is_set()):
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                _state.connected = True
                _state.last_error = None
                _state.last_tick_at = time.monotonic()
                backoff = 1.0
                for i in range(0, len(subs), MAX_BATCH):
                    batch = subs[i : i + MAX_BATCH]
                    await ws.send(json.dumps({"RequestCode": SUBSCRIBE_QUOTE,
                                              "InstrumentCount": len(batch),
                                              "InstrumentList": batch}))
                last_flush = time.monotonic()
                while not (stop and stop.is_set()):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=STALL_SECONDS)
                    except asyncio.TimeoutError:
                        _state.last_error = "no data — reconnecting"
                        break
                    if not isinstance(raw, (bytes, bytearray)):
                        continue
                    packets = parse_packet(bytes(raw))
                    _state.packets += len(packets)
                    for pkt in packets:
                        if pkt["type"] == "disconnect":
                            _state.last_error = "server sent disconnect"
                            break
                        _state.ticks += 1
                        _state.last_tick_at = time.monotonic()
                        buffer.append(pkt)
                        if on_tick:
                            try:
                                on_tick(pkt)
                            except Exception:
                                pass
                    if time.monotonic() - last_flush >= FLUSH_SECONDS or len(buffer) >= 500:
                        _state.flushed += await _flush(buffer, sec_map)
                        last_flush = time.monotonic()
                await _flush(buffer, sec_map)
        except Exception as exc:
            _state.last_error = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            _state.connected = False
        if stop and stop.is_set():
            break
        _state.reconnects += 1
        await asyncio.sleep(backoff)
        backoff = min(MAX_BACKOFF, backoff * 2)


if __name__ == "__main__":  # ponytail self-check
    # a ticker packet: header(code=2, len=16, seg=0, sec=13) + ltp + ltt
    tick = struct.pack("<BHBI", CODE_TICKER, 16, 0, 13) + struct.pack("<fI", 24175.65, 1756400000)
    got = parse_packet(tick)
    assert len(got) == 1 and got[0]["type"] == "ticker"
    assert got[0]["ltp"] == 24175.65 and got[0]["security_id"] == 13

    quote_payload = struct.pack("<fHIfIIIffff", 24180.5, 50, 1756400001, 24170.0,
                                123456, 700, 800, 24100.0, 24050.0, 24250.0, 24000.0)
    quote = struct.pack("<BHBI", CODE_QUOTE, HEADER + len(quote_payload), 0, 13) + quote_payload
    q = parse_packet(quote)[0]
    assert q["type"] == "quote" and q["ltp"] == 24180.5 and q["volume"] == 123456
    assert q["high"] == 24250.0 and q["low"] == 24000.0

    # two packets in one frame must both decode
    both = parse_packet(tick + quote)
    assert len(both) == 2, both

    # malformed input must not raise
    assert parse_packet(b"") == []
    assert parse_packet(b"\x02\xff") == []
    assert parse_packet(struct.pack("<BHBI", CODE_TICKER, 9999, 0, 13)) == []

    oi = struct.pack("<BHBI", CODE_OI, 12, 0, 13) + struct.pack("<I", 202780)
    assert parse_packet(oi)[0]["oi"] == 202780
    assert parse_packet(struct.pack("<BHBI", CODE_DISCONNECT, 8, 0, 13))[0]["type"] == "disconnect"

    s = status()
    assert s["enabled"] is False and s["connected"] is False
    print("tick_feed.py self-check ok — packet decoding verified against openalgo layout")
