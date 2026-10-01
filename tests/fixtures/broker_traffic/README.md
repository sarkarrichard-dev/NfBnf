# Broker traffic fixtures

Redacted recordings of real broker REST/feed traffic, replayed by the test
suite through the real client code (`tests/_fake_brokers.py`) instead of a
Black-Scholes-proxy or a hand-written stub. This is D-03 — tests exercise the
real signing, retry, error-handling and result-unwrapping code, only the
network hop is faked.

## Files

| File | Broker | Added by | Holds |
|---|---|---|---|
| `delta_rest.jsonl` | Delta Exchange | plan 02-01 | `GET /v2/wallet/balances`, `/v2/positions/margined`, `/v2/fills`, `/v2/orders` (open), `/v2/orders/history` |
| `dhan_rest.jsonl` | Dhan | plan 02-03 | Dhan REST order/position/fund endpoints, plus real order rows extracted from the app's own SQLite journal merged in (rows carry `"source": "journal"` — not a live capture, see `source` below) |
| `dhan_feed.jsonl` | Dhan | plan 02-06 | Raw websocket feed frames, base64-encoded |

## Row schema

Every row is one JSON object per line (JSONL). REST rows:

```json
{
  "captured_at": "2026-09-30T12:00:00+00:00",
  "broker": "delta",
  "source": "captured",
  "method": "GET",
  "path": "/v2/orders/history",
  "params": {"page_size": "50"},
  "status": 200,
  "body": {}
}
```

A feed row carries `frame_b64` (the raw websocket frame, base64-encoded)
instead of `method`/`path`/`params`/`body`.

## `source` values

- **`captured`** — recorded live by `scripts/capture_broker_traffic.py`
  against the real broker account (read-only GET calls only).
- **`journal`** — real responses extracted from the app's own journal
  (`memory/`), for order shapes a live GET capture cannot reach (e.g. a
  historical fill).
- **`reference`** — a response shape copied from the cited
  `reference/openalgo/...:line` file because the live account has no such
  data to capture (e.g. an account with no open orders cannot capture what
  an open order row looks like).

## Re-capturing

```
python -m scripts.capture_broker_traffic --broker delta
```

Requires `DELTA_API_KEY`/`DELTA_API_SECRET` in `.env`. Read-only: the
recording transport refuses to send anything but a GET request, so this can
never place, cancel or modify a real order. The script redacts every row
before writing (see `REDACT_KEYS` in the script) and then re-reads the
written file — if any configured secret string still appears in it, the file
is deleted and the script exits 1 rather than leaving a leaking fixture on
disk.

## Redaction enforcement

`tests/test_broker_traffic_fixtures.py` fails the suite if any `*.jsonl`
file here contains an un-redacted key from `REDACT_KEYS`, a JWT-shaped
string, or a 64-character hex string (a raw HMAC signature) — so a fixture
that leaks a real credential cannot silently stay committed.
