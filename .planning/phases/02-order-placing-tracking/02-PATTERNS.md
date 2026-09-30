# Phase 2: Order Placing & Tracking - Pattern Map

**Mapped:** 2026-09-30
**Files analyzed:** 9
**Analogs found:** 9 / 9

Note: all analog paths below were spot-checked against git tracking (`git ls-files`)
— none are gitignored mirrors; all live under the normally-tracked `index_ai/`,
`crypto/`, `tests/`, `dashboard/src/` trees.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `crypto/delta/client.py` (add `cancel_order`, `order_history`/`order_status`) | service (broker client method) | request-response | same file's `margin_required`/`positions` signed-GET methods | exact (same class, same call shape) |
| `index_ai/dhan.py` (add `cancel_order`) | service (broker client method) | request-response | same file's `get_order` (lines 198-202) | exact (same class, same `_request` call shape) |
| `index_ai/executor.py` or a new cancel-path function (ORD-01 lock usage) | service | request-response, event-driven (race-safe) | `index_ai/executor.py:208-227` `execute_plan`'s lock block | exact — must acquire the **same** `execution_safety.acquire_execution_lock` object |
| `index_ai/reconcile.py::reconcile()` (add `notify.alert`) | service | event-driven | `crypto/executor.py:223-258` `reconcile()` | exact — same function name, same problem, crypto side already correct |
| `crypto/lanes.py` / `crypto/executor.py` (D-08 stuck-position timeout) | service | event-driven | `crypto/executor.py:166-175` `position_state()` + `crypto/lanes.py` `_reap_exchange_close` (~lines 692-720) | exact — extend existing `"unknown"` branch, don't invent a new check |
| `dashboard/src/components/shell/StatusPills.tsx` (add tick-status pill) | component | request-response (polling) | same file's existing `Pill tone={dhanReady...}` block | exact |
| `tests/_fake_brokers.py` (new shared fake-client helper) | test utility | request-response (stub) | `tests/test_dhan_account_api.py:11-18` `FakeDhan`/`FakeCfg` inline classes | role-match (closest existing hand-rolled stub pattern; no shared fixture exists yet) |
| `tests/fixtures/broker_traffic/` (new capture fixtures) + a JSONL capture wrapper | test fixture / utility | file-I/O | `crypto/journal.py`'s JSONL-append pattern (cited in RESEARCH.md; no direct raw-traffic capture exists) | partial — no true analog exists, nearest structural sibling is the JSONL journal style |
| `tests/test_order_cancel_race.py`, extensions to `tests/test_tick_feed.py`, `tests/test_scan_health_reconcile.py`, `tests/test_tick_driven_stops.py` | test | request-response / event-driven | `tests/test_tick_driven_stops.py` (monkeypatch style, no mocking lib) | exact |

## Pattern Assignments

### `crypto/delta/client.py` — add `cancel_order` (service, request-response)

**Analog:** same file, `margin_required`/`positions`/`wallet` (lines 147-163)

**Core pattern to copy** (lines 147-163):
```python
def wallet(self) -> list[dict[str, Any]]:
    return self.signed("GET", "/v2/wallet/balances")

def positions(self) -> list[dict[str, Any]]:
    return self.signed("GET", "/v2/positions/margined")

def margin_required(
    self, product_id: int, size: int, side: str, order_type: str = "market_order"
) -> dict[str, Any]:
    return self.signed(
        "GET",
        f"/v2/products/{product_id}/margin_required",
        params={"size": size, "side": side, "order_type": order_type},
    )
```
New method follows the exact same one-liner-over-`self.signed()` shape. Per
RESEARCH.md Finding 2/Assumption, the real Delta contract (from the vendored
reference) is `DELETE /v2/orders` with body `{"id": <order_id>, "product_id":
<product_id>}`:
```python
def cancel_order(self, order_id: int, product_id: int) -> dict[str, Any]:
    return self.signed("DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id})
```
For the D-06 tie-breaker on Delta (no ready-made order-status call exists),
either add `order_history()` wrapping `GET /v2/orders/history` the same
one-liner way, or — the simpler, RESEARCH.md-recommended route — compose the
tie-breaker out of the already-existing `executor.position_state()` +
`executor.fill_report()` (see below) rather than adding a new client method.

**Imports pattern** (lines 1-27): module already imports only `httpx`,
`hashlib`/`hmac`/`json`/`time`/`logging`, and `crypto.config` — no new import
needed for a `cancel_order` method.

**Self-check pattern** (lines 244-266): this file's own `if __name__ ==
"__main__":` block is the project's lightweight-test convention for this
module — extend it with an assertion that `cancel_order`'s method exists /
builds the right body shape without a live network call, matching the
ponytail "one runnable check" requirement.

---

### `index_ai/dhan.py` — add `cancel_order` (service, request-response)

**Analog:** same file, `get_order` (lines 198-202)

**Core pattern to copy** (lines 198-202):
```python
def get_order(self, order_id: str) -> dict[str, Any]:
    oid = str(order_id or "").strip()
    if not oid:
        raise ValueError("order_id is required")
    return self._request("GET", f"/orders/{oid}", context="order status")
```
New method mirrors this exactly, swapping method/path:
```python
def cancel_order(self, order_id: str) -> dict[str, Any]:
    oid = str(order_id or "").strip()
    if not oid:
        raise ValueError("order_id is required")
    return self._request("DELETE", f"/orders/{oid}", context="cancel order")
```
Note: `_request` (lines 94-130, read this session) only branches on GET vs.
"else → POST" today (`if method.upper() == "GET": ... else: client.post(...)`)
— this is a real gap the cancel implementation must close (add a DELETE
branch using `client.delete(url, headers=headers)`), not something to paper
over. Flag this in the plan rather than silently assuming `_request` already
supports DELETE.

**Error handling / retry pattern** (lines 94-130): existing `_request`
already wraps `httpx.HTTPError`, retries up to `_MAX_RETRIES`, and classifies
via `classify_http_error`/`explain_dhan_http_error` — the new DELETE branch
reuses this same wrapper, no new error handling needed.

---

### ORD-01 cancel-race lock (executor / new cancel path)

**Analog:** `index_ai/executor.py:208-227` (read this session per RESEARCH.md)

**Core pattern to copy**:
```python
instrument_key = str(plan.option.get("instrument") or inst.key)
lock = acquire_execution_lock(instrument_key)
status = "PAPER_RECORDED"
broker_response: dict[str, Any] | None = None

with lock:
    safety = validate_execution_plan(...)
    if not safety.ok:
        return {"status": "BLOCKED", ...}
    if trade_mode == "LIVE":
        ...
        broker_response = place_live_entry_orders(...)
```
The cancel path (wherever it lives — likely a new function in `executor.py`
or `dhan_orders.py`/`crypto/executor.py`) must call
`execution_safety.acquire_execution_lock(instrument_key)` with the **same**
key shape as entry, and wrap the cancel call + any journal update inside the
`with lock:` block — this is what actually prevents the race, per
Pitfall 2 in RESEARCH.md. Do not instantiate a separate `threading.Lock()`.

---

### `index_ai/reconcile.py::reconcile()` — add notify.alert (ORD-02/D-09)

**Analog:** `crypto/executor.py:223-258` (already correct)

**Exact excerpt to mirror**:
```python
# Source: crypto/executor.py:223-258
def reconcile(client: DeltaClient) -> list[str]:
    ...
    issues: list[str] = []
    for sym, key in local.items():
        if sym not in delta_open:
            issues.append(f"local {key} open but Delta shows FLAT for {sym}")
    ...
    if issues:
        logger.error("crypto reconcile mismatch: %s", " | ".join(issues))
        try:
            from index_ai import notify
            notify.alert("⚠️ <b>CRYPTO RECONCILE</b>\n" + "\n".join(issues), key="reconcile")
        except Exception:
            pass
    return issues
```
`index_ai/reconcile.py::reconcile()` already builds an equivalent `issues`
list (net-position drift) — add the same `notify.alert(..., key=f"reconcile:
{security_id}")` call (per-security key, per RESEARCH.md's dedup
recommendation) right where it currently only logs. Import `notify` lazily
inside the function exactly as crypto does (avoids a module-level import
cycle risk) and wrap in `try/except Exception: pass` so a Telegram failure
never breaks reconciliation itself.

**Shared pattern source:** `index_ai/notify.py:159-162` —
`notify.alert(text, *, key, window_s=3600.0)` — "a one-off operational alert
… de-duped per key so a stuck loop cannot spam it." Applies to all new
reconcile/D-09 call sites.

---

### D-08 crypto stuck-position timeout

**Analog:** `crypto/executor.py:166-175` `position_state()` (three-string
return already documented: `"open" | "flat" | "unknown"`) + `crypto/lanes.py`
`_reap_exchange_close` guard (`if executor.position_state(...) != "flat":
return False`).

Extend the existing `"unknown"` branch with an elapsed-time check (e.g. track
first-seen-unknown timestamp per symbol, escalate via `notify.alert` and/or
force a fresh `position_state()` call once elapsed exceeds the chosen
timeout — RESEARCH.md recommends ~3-5x the 60s crypto scan cadence, i.e.
~3-5 minutes). Do not add a parallel reachability check outside
`position_state()`'s existing three-value contract (Pitfall 3).

---

### `dashboard/src/components/shell/StatusPills.tsx` — tick-status pill (D-10)

**Analog:** same file, existing `Pill` usage.

**Exact excerpt to copy the shape of**:
```tsx
<Pill tone={dhanReady ? 'good' : 'warn'} title="Dhan broker connection">
  Dhan {dhanReady ? 'OK' : '—'}
</Pill>
```
New pill, fed by `GET /api/tick-feed` (`enabled`/`connected`/`stalled`
fields, server-side already returns these per `index_ai/server.py:1465-1470`
— no backend change needed):
```tsx
<Pill tone={stalled ? 'warn' : connected ? 'good' : 'idle'} title="Tick feed status">
  Ticks {stalled ? 'fallback' : connected ? 'live' : 'off'}
</Pill>
```
Wire via a `useQuery(['tick-feed'], () => api('/api/tick-feed'))` alongside
the existing `/api/status` poll (or piggyback per RESEARCH.md's note), and
pass the derived props down from `App.tsx:211-217`'s existing
`topRight={<StatusPills tradingMode=... market=... dhanReady=... />}` call
site the same way `dhanReady` is threaded today.

---

### `tests/_fake_brokers.py` — new shared fake-client helper

**Analog:** `tests/test_dhan_account_api.py:11-18` (closest existing
hand-rolled stub in the suite)

**Exact excerpt to generalize**:
```python
class FakeDhan:
    ready = False
    access_token = ""
    app_credentials_ready = False
    can_generate_consent = False

class FakeCfg:
    dhan = FakeDhan()
```
No shared fixture module exists today (RESEARCH.md Wave 0 Gap) — every test
file hand-rolls its own stub class inline, this is the only pre-existing
precedent for the shape. Build `tests/_fake_brokers.py` as plain classes
(`FakeDhanClient`, `FakeDeltaClient`) with the same "attributes/methods
stubbed directly, no mocking library" convention, parametrized to accept
injected exceptions/delays at a given call index (for D-03 fault injection),
then import it from the four new/extended test files instead of
re-implementing. Match this project's established "no `responses`/`respx`/
`pytest-httpx`" rule (per RESEARCH.md's Don't Hand-Roll and Standard Stack
sections) — plain classes only.

---

### `tests/fixtures/broker_traffic/` — raw traffic capture (D-03 infra)

**No direct analog exists** (confirmed by RESEARCH.md: `market_log.py`'s
four SQLite tables never capture raw order responses). Nearest structural
sibling is `crypto/journal.py`'s JSONL-append style (per RESEARCH.md's own
recommendation) — use a flat JSONL file per broker under
`tests/fixtures/broker_traffic/` (e.g. `dhan_cancel_race.jsonl`,
`delta_reconnect.jsonl`), each line `{"timestamp", "broker", "endpoint",
"request", "response"}`, **with `access-token` (Dhan) / `api-key`+
`signature` (Delta) redacted before ever being written** — see Security
Domain V9 in RESEARCH.md. This is fixture data + a small one-off capture
script (not production code), matching D-03's locked scope (replay-only, no
always-on production capture).

## Shared Patterns

### Money-path lock reuse (ORD-01)
**Source:** `index_ai/execution_safety.py:25-26,59-65,590-592` —
`acquire_execution_lock(instrument_key)`, a per-instrument `threading.Lock`.
**Apply to:** any new cancel-path code, wherever it lives.

### Telegram alert de-dup (D-09 / D-08 escalation)
**Source:** `index_ai/notify.py:159-162` — `notify.alert(text, *, key,
window_s=3600.0)`.
**Apply to:** `index_ai/reconcile.py::reconcile()`, any new D-08 crypto
stuck-position escalation.

### Sync client + async handler boundary
**Source:** CLAUDE.md "Never put blocking I/O in an `async def` handler" +
existing call sites `index_ai/scanner.py:696`, `index_ai/server.py:984`
(`asyncio.to_thread(...)`).
**Apply to:** any new cancel/status-check call added to a FastAPI `async def`
handler — both `DhanClient` and `DeltaClient` are synchronous (`httpx.Client`).

### No-mocking-library test style
**Source:** `tests/test_tick_driven_stops.py` (monkeypatch-based), `tests/
test_dhan_account_api.py` (inline Fake classes).
**Apply to:** `tests/_fake_brokers.py` and all four new/extended test files —
no `responses`/`respx`/`pytest-httpx`/`hypothesis`.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `tests/fixtures/broker_traffic/*.jsonl` capture format itself | test fixture | file-I/O | No raw-broker-traffic capture exists anywhere in the codebase today (RESEARCH.md Finding 4) — nearest sibling is `crypto/journal.py`'s JSONL style, used only as a structural reference, not a literal template |

## Metadata

**Analog search scope:** `index_ai/` (dhan.py, dhan_orders.py, reconcile.py,
executor.py, execution_safety.py, tick_feed.py, scanner.py, notify.py,
server.py), `crypto/` (delta/client.py, executor.py, lanes.py),
`dashboard/src/components/shell/StatusPills.tsx`, `dashboard/src/App.tsx`,
`tests/` (test_dhan_account_api.py, test_tick_driven_stops.py,
test_scan_health_reconcile.py, test_tick_feed.py, test_dhan_orders.py,
conftest.py)
**Files scanned:** ~20 (all already read in full or targeted this session by
RESEARCH.md; this pass added `index_ai/dhan.py:1-130,198-392`,
`crypto/delta/client.py` full file, `tests/test_dhan_account_api.py` full
file)
**Pattern extraction date:** 2026-09-30
