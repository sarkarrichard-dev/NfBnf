"""Records real, GET-only broker REST traffic into a redacted JSONL fixture
the test suite replays (D-03). Read-only by construction: the transport
refuses anything that is not a GET before it ever reaches the network, so
this script cannot place, cancel or modify a real order.

    python -m scripts.capture_broker_traffic --broker delta

Only ``--broker delta`` exists this plan. Plan 02-03 adds ``--broker dhan``
(REST + journal extraction) and plan 02-06 adds ``--feed-seconds`` (raw feed
frames) to this same file/script.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", choices=["delta"], required=True)
    args = parser.parse_args(argv)
    if args.broker == "delta":
        return capture_delta()
    print(f"unknown --broker {args.broker!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
