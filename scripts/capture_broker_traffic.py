"""Records real, GET-only broker REST traffic into a redacted JSONL fixture
the test suite replays (D-03). Read-only by construction: the transport
refuses anything that is not a GET before it ever reaches the network, so
this script cannot place, cancel or modify a real order.

    python -m scripts.capture_broker_traffic --broker delta
    python -m scripts.capture_broker_traffic --broker dhan
    python -m scripts.capture_broker_traffic --broker all
    python -m scripts.capture_broker_traffic --broker feed --feed-seconds 20

``--broker dhan`` also extracts real placement/status responses already
sitting in the app's own journal (a live GET capture cannot reach a
historical fill). ``--broker feed`` (plan 02-06) listens to the real Dhan
tick-feed websocket read-only for ``--feed-seconds`` and records raw binary
frames as base64 — it never touches ``delta_rest.jsonl``/``dhan_rest.jsonl``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "broker_traffic"

# Keys whose values are replaced with "REDACTED" wherever they appear, at any
# depth, in a captured row. Compared lower-case against the row's own keys.
REDACT_KEYS: frozenset[str] = frozenset(
    {
        "access-token",
        "access_token",
        "accesstoken",
        "client-id",
        "client_id",
        "clientid",
        "dhanclientid",
        "api-key",
        "api_key",
        "apikey",
        "api-secret",
        "api_secret",
        "apisecret",
        "signature",
        "token",
        "user_id",
        "userid",
        "email",
        "phone",
        "mobile",
        "pan",
    }
)


def redact(obj: Any, secrets: list[str]) -> Any:
    """Recursively replace any ``REDACT_KEYS`` value, and any string that
    contains one of ``secrets`` verbatim, with ``"REDACTED"``."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in REDACT_KEYS:
                out[k] = "REDACTED"
            else:
                out[k] = redact(v, secrets)
        return out
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        for secret in secrets:
            if secret and secret in obj:
                return "REDACTED"
        return obj
    return obj


class RecordingTransport(httpx.BaseTransport):
    """Wraps a real ``httpx`` transport; forwards GET requests and records
    the response. Refuses (raises) any non-GET request before sending it —
    capture is read-only, it must never be able to place a live order."""

    def __init__(self, inner: httpx.BaseTransport, *, broker: str) -> None:
        self._inner = inner
        self._broker = broker
        self.rows: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() != "GET":
            raise RuntimeError(f"capture is read-only — refused {request.method} {request.url}")
        response = self._inner.handle_request(request)
        response.read()
        try:
            body: Any = json.loads(response.content)
        except ValueError:
            body = response.text
        self.rows.append(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "broker": self._broker,
                "source": "captured",
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "status": response.status_code,
                "body": body,
            }
        )
        return response


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")


def _secret_leaked(path: Path, secrets: list[str]) -> bool:
    text = path.read_text(encoding="utf-8")
    return any(secret and secret in text for secret in secrets)


def capture_delta() -> int:
    from crypto.config import crypto_settings
    from crypto.delta.client import DeltaClient, DeltaError

    s = crypto_settings()
    if not s.credentials_ready:
        print("DELTA_API_KEY / DELTA_API_SECRET not configured — nothing to capture.")
        return 1

    inner = httpx.HTTPTransport(local_address="0.0.0.0")
    rec = RecordingTransport(inner, broker="delta")
    client = DeltaClient(s, transport=rec)
    calls = (
        ("/v2/wallet/balances", client.wallet),
        ("/v2/positions/margined", client.positions),
        ("/v2/fills", client.fills),
        ("/v2/orders", client.open_orders),
        ("/v2/orders/history", client.order_history),
    )
    for path, fn in calls:
        try:
            result = fn()
            count = len(result) if isinstance(result, list) else (1 if result else 0)
            print(f"{path}: status=200 count={count}")
        except DeltaError as exc:
            print(f"{path}: status={exc.status} count=0")
    client.close()

    secrets = [s.api_key, s.api_secret]
    rows = [redact(r, secrets) for r in rec.rows]
    out_path = FIXTURES_DIR / "delta_rest.jsonl"
    _write_jsonl(out_path, rows)

    if _secret_leaked(out_path, secrets):
        out_path.unlink(missing_ok=True)
        print("REFUSING TO COMMIT: a configured secret was found in the written file — deleted.")
        return 1

    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


def _dhan_journal_rows() -> list[dict[str, Any]]:
    """Real placement/status responses already sitting in the app's own
    journal (source "journal") -- a live GET capture cannot reach a
    historical fill. Read-only: opened via a sqlite3 URI in mode=ro so this
    can never write to the real journal."""
    import sqlite3

    from index_ai.config import DB_PATH

    rows: list[dict[str, Any]] = []
    if not DB_PATH.exists():
        return rows
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        db_rows = conn.execute(
            """
            SELECT option_json FROM trades
            WHERE upper(mode) = 'LIVE' AND option_json LIKE '%broker_orders%'
            ORDER BY created_at DESC LIMIT 10
            """
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"journal extraction skipped: {exc}")
        return rows

    for r in db_rows:
        try:
            option = json.loads(r["option_json"])
        except Exception:
            continue
        broker_orders = option.get("broker_orders")
        if not isinstance(broker_orders, dict):
            continue
        entries: list[dict[str, Any]] = []
        legs = broker_orders.get("legs")
        if isinstance(legs, list) and legs:
            for leg in legs:
                if isinstance(leg, dict):
                    entries.append(leg)
        elif isinstance(broker_orders.get("response"), dict):
            entries.append(broker_orders)

        for entry in entries:
            raw_reply = entry.get("raw") if isinstance(entry.get("raw"), dict) else entry.get(
                "response"
            )
            final_reply = entry.get("response") if isinstance(entry.get("response"), dict) else raw_reply
            captured_at = datetime.now(timezone.utc).isoformat()
            if isinstance(raw_reply, dict):
                rows.append(
                    {
                        "captured_at": captured_at,
                        "broker": "dhan",
                        "source": "journal",
                        "method": "POST",
                        "path": "/v2/orders",
                        "params": {},
                        "status": 200,
                        "body": raw_reply,
                    }
                )
            order_id = (final_reply or {}).get("orderId") if isinstance(final_reply, dict) else None
            if order_id:
                rows.append(
                    {
                        "captured_at": captured_at,
                        "broker": "dhan",
                        "source": "journal",
                        "method": "GET",
                        "path": f"/v2/orders/{order_id}",
                        "params": {},
                        "status": 200,
                        "body": final_reply,
                    }
                )
    return rows


def capture_dhan() -> int:
    from index_ai.config import settings
    from index_ai.dhan import DhanClient

    cfg = settings()
    if not cfg.dhan.ready:
        print("Dhan client-id / access-token not configured — nothing to capture.")
        return 1

    inner = httpx.HTTPTransport(local_address="0.0.0.0")
    rec = RecordingTransport(inner, broker="dhan")
    client = DhanClient(cfg.dhan, transport=rec)
    calls = (
        ("/v2/orders", client.list_today_orders),
        ("/v2/trades", client.list_today_trades),
        ("/v2/positions", client.list_positions),
    )
    for path, fn in calls:
        try:
            result = fn()
            count = len(result) if isinstance(result, list) else (1 if result else 0)
            print(f"{path}: status=200 count={count}")
        except Exception as exc:
            print(f"{path}: error={exc}")

    journal_rows = _dhan_journal_rows()

    secrets = [cfg.dhan.access_token, str(cfg.dhan.client_id)]
    rows = [redact(r, secrets) for r in rec.rows] + [redact(r, secrets) for r in journal_rows]
    out_path = FIXTURES_DIR / "dhan_rest.jsonl"
    _write_jsonl(out_path, rows)

    if _secret_leaked(out_path, secrets):
        out_path.unlink(missing_ok=True)
        print("REFUSING TO COMMIT: a configured secret was found in the written file — deleted.")
        return 1

    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


async def _capture_feed_frames(feed_seconds: int) -> list[bytes]:
    """Read-only: connects, subscribes, and only ever calls ``recv()`` — never
    sends anything beyond the standard subscribe message. Stops at
    ``feed_seconds`` or 400 frames, whichever comes first."""
    import websockets

    from index_ai.config import settings
    from index_ai.tick_feed import FEED_URL, MAX_BATCH, SUBSCRIBE_QUOTE, _subscription_list

    cfg = settings()
    subs = _subscription_list()
    if not subs:
        return []

    url = (
        f"{FEED_URL}?"
        + urlencode(
            {
                "version": "2",
                "token": cfg.dhan.access_token,
                "clientId": str(cfg.dhan.client_id),
                "authType": "2",
            }
        )
    )
    frames: list[bytes] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + feed_seconds
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        for i in range(0, len(subs), MAX_BATCH):
            batch = subs[i : i + MAX_BATCH]
            await ws.send(
                json.dumps(
                    {
                        "RequestCode": SUBSCRIBE_QUOTE,
                        "InstrumentCount": len(batch),
                        "InstrumentList": batch,
                    }
                )
            )
        while len(frames) < 400:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                frames.append(bytes(raw))
    return frames


def capture_feed(feed_seconds: int = 20) -> int:
    from index_ai.config import settings
    from index_ai.tick_feed import parse_packet

    cfg = settings()
    if not cfg.dhan.ready:
        print("Dhan client-id / access-token not configured — nothing to capture.")
        return 1

    try:
        frames = asyncio.run(_capture_feed_frames(feed_seconds))
    except Exception as exc:
        print(f"feed capture failed: {type(exc).__name__}: {exc}")
        return 1

    if not frames:
        print("captured zero frames — feed access or token problem. Not writing a fixture.")
        return 1

    rows = [
        {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "broker": "dhan",
            "source": "captured",
            "channel": "feed",
            "frame_b64": base64.b64encode(f).decode("ascii"),
        }
        for f in frames
    ]
    out_path = FIXTURES_DIR / "dhan_feed.jsonl"
    _write_jsonl(out_path, rows)

    secrets = [cfg.dhan.access_token, str(cfg.dhan.client_id)]
    if _secret_leaked(out_path, secrets):
        out_path.unlink(missing_ok=True)
        print("REFUSING TO COMMIT: a configured secret was found in the written file — deleted.")
        return 1

    types_seen: dict[str, int] = {}
    for f in frames:
        for pkt in parse_packet(f):
            t = pkt.get("type", "unknown")
            types_seen[t] = types_seen.get(t, 0) + 1
    print(f"captured {len(frames)} frames — packet types: {types_seen}")
    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", choices=["delta", "dhan", "all", "feed"], required=True)
    parser.add_argument("--feed-seconds", type=int, default=20)
    args = parser.parse_args(argv)
    if args.broker == "delta":
        return capture_delta()
    if args.broker == "dhan":
        return capture_dhan()
    if args.broker == "all":
        rc_delta = capture_delta()
        rc_dhan = capture_dhan()
        return rc_delta or rc_dhan
    if args.broker == "feed":
        return capture_feed(args.feed_seconds)
    print(f"unknown --broker {args.broker!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
