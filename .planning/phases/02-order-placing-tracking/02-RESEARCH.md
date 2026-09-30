# Phase 2: Order Placing & Tracking - Research

**Researched:** 2026-09-30
**Domain:** Broker order-placement/tracking hardening (Dhan REST+websocket, Delta Exchange REST) — fault-injection testing, not new trading logic
**Confidence:** HIGH (all claims below are grounded in files read this session; no external library research was needed)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Both broker connections are in scope this phase — the shared Dhan
  connection (India options/futures + MCX commodities: `executor.py`,
  `dhan_orders.py`, `dhan.py`) AND the separate Delta Exchange connection
  (crypto). Originally the roadmap's own file references (CONCERNS.md) implied
  Dhan-only; Richard explicitly widened this to include crypto.
- **D-02:** Crypto/Delta is fixed **first**, ahead of Dhan — Richard's stated
  reason: crypto is the one aiming to arm real money soonest (targeting
  ~November 2026 per `memory/project-algo-bnf-vision.md`), so its
  order-placement path should be hardened before it carries real risk, even
  though Dhan has been live longer.
- **D-03:** Tests replay **real recorded broker traffic** (Dhan and/or Delta
  order responses, websocket messages) with deliberately injected faults
  (dropped connection, delayed response, out-of-order delivery) — not a
  from-scratch fake/mock client. Research confirmed: no such capture exists
  today (see "Critical Finding: D-03" below) — building it is part of this
  phase's own work.
- **D-04:** The proof bar for "fixed" is a **test that simulates the exact
  failure and passes** — no live paper-trading soak period required on top of
  that.
- **D-05:** Within each broker, order-cancel races (ORD-01) and position
  reconciliation after a disconnect (ORD-02) are fixed before websocket
  reconnection under load (ORD-03) and the tick-feed fallback (ORD-04).
- **D-06:** When the code genuinely cannot tell whether an order went through
  after a disconnect, it queries the broker's own order-status API to break
  the tie, rather than silently assuming "not filled."
- **D-07 (Claude's discretion, recorded so it isn't silently invented later):**
  If the order-status tie-breaker API is *also* unreachable, fall back to
  treating the order as not filled rather than risk a duplicate.
- **D-08:** Crypto gets an extra safeguard India/commodities don't need: a
  maximum time an unclear/stuck position can sit before the system forces a
  fresh broker-status check or surfaces it. Exact timeout value left to
  Claude's discretion (research should pick something short relative to
  crypto's own scan cadence — see "Findings for D-08" below).
- **D-09:** A detected reconciliation problem sends a Telegram message, the
  same way trade open/close already does.
- **D-10:** Written up in the existing docs (Strategy-Guide-style) **and** a
  simple dashboard indicator this phase (e.g. "ticks: live" vs. "ticks: candle
  fallback") — not just the guide alone. The fuller "Data Health" dashboard
  view is Phase 4's job (UIUX-02), not this phase's.

### Claude's Discretion
- Exact mechanism for capturing/replaying real broker traffic (D-03).
- Exact crypto stuck-position timeout value (D-08).
- Exact wording/placement of the tick-status dashboard indicator (D-10).

### Deferred Ideas (OUT OF SCOPE)
- The fuller "Data Health" dashboard view (tick age, option-chain snapshot
  age, measured spread age, full Dhan websocket status all in one place) —
  explicitly Phase 4's job (UIUX-02).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ORD-01 | Order placement handles a cancel-issued-while-placing race without creating a duplicate or an orphaned order | **No cancel function exists anywhere in this codebase for either broker** (confirmed by exhaustive grep — see Critical Finding below). `execution_safety.acquire_execution_lock` (per-instrument `threading.Lock`) is the only existing concurrency primitive on the entry path and only guards entry, not cancel. This requirement is new-mechanism work, not a bugfix. |
| ORD-02 | A position is correctly reconciled after a broker disconnection | `index_ai/reconcile.py` (net-position drift, 4 classes) + `dhan_orders.sync_open_live_trades` (order-status sync) already exist and are scheduled (`scanner._run_reconcile`), but neither is fault-injection tested and neither notifies on drift (only logs). Crypto's `crypto/executor.reconcile` is the read-only equivalent, already notifies via `notify.alert`, also untested against injected faults. |
| ORD-03 | Dhan websocket reconnects and recovers missed ticks during a busy scan cycle without silently dropping data | `index_ai/tick_feed.py::run_feed` (not `dhan.py` — see Critical Finding). Has stall detection + exponential backoff already. **Found a real data-loss bug**: on a hard `ConnectionClosed`-style exception (vs. a graceful stall timeout), the buffered-but-unflushed ticks are dropped (see Critical Finding: tick_feed flush gap). |
| ORD-04 | Tick-driven stop triggering degrades safely to the candle-based fallback, documented | Three-tier cadence already exists (tick-instant → `_fast_trail_loop` every 20s → full scan "trails" stage every 90s) — the fallback substantially already works. What's missing is a test proving it explicitly, the doc, and the dashboard indicator (D-10) — `/api/tick-feed` endpoint already exists server-side but the dashboard never calls it. |
</phase_requirements>

## Summary

This is a hardening phase on code that already does more than CONTEXT.md's
"Existing Code Insights" section assumed — two of its file pointers are
corrections the planner needs: the Dhan **websocket** client lives in
`index_ai/tick_feed.py`, not `dhan.py` (which is REST-only); and the
reconcile functions live in the dedicated `index_ai/reconcile.py` module
(net-position drift) plus `dhan_orders.py` (order-status sync), not
`learning.py` (which only holds the journal read/write helpers those two
modules call into).

The single biggest real finding: **neither broker connection has a cancel
function anywhere in this codebase.** Dhan's `DhanClient` (`index_ai/dhan.py`)
exposes `place_market_order`, `get_order`, `list_today_orders`,
`list_today_trades`, `trades_for_order`, `list_positions` — no `cancel_order`.
`crypto/delta/client.py`'s `DeltaClient` exposes `wallet`, `positions`,
`fills`, `margin_required`, plus the generic `signed()` — no cancel wrapper,
though the vendored `reference/openalgo/broker/deltaexchange/api/order_api.py`
shows the real Delta cancel call (`DELETE /v2/orders` with
`{id, product_id}`). This means ORD-01 is not "fix a race in existing cancel
code" — it is "the planner must design and build a minimal cancel path, and
the race-safety property (lock the same per-instrument/per-trade section the
placement holds) is the actual deliverable," not a pre-existing bug to patch.

A second concrete, verified bug was found while tracing ORD-03:
`tick_feed.run_feed`'s inner loop only flushes its tick buffer after the inner
`while` exits via the `TimeoutError` (stall) branch or a clean `stop` signal —
if `ws.recv()` raises any other exception (e.g. a hard `ConnectionClosed`),
control jumps straight to the outer `except Exception` and the pending,
unflushed buffer (up to `FLUSH_SECONDS=5` worth, or up to 500 ticks) is
silently dropped. This is the exact "websocket reconnecting mid-scan-cycle
silently drops tick data" scenario ORD-03's success criterion describes, with
a file:line fix target, not a hypothetical.

D-03's raw-broker-traffic question resolved firmly: `market_log.py` records
four tables (`observations`, `decisions`, `ticks`, `chain`) — all high-level
decision/market snapshots, never a raw order-placement response or raw
websocket frame. No such capture exists for either broker. Building it (even
a minimal JSONL recorder wrapping `DhanClient`/`DeltaClient` calls, since
no `hypothesis`-style traffic-replay library is installed or needed) is
this phase's own prerequisite work, exactly as CONTEXT.md anticipated.

**Primary recommendation:** Treat this phase as building three small, testable
primitives — (1) a cancel path with an instrument/trade-scoped lock shared
with the entry path, (2) a raw-traffic capture+replay harness thin enough to
wrap the two existing REST clients, (3) a stuck-position timeout for crypto —
then writing fault-injection tests against each of the four requirements using
that harness, reusing the existing `monkeypatch`-based test style (no new
test framework or dependency needed). Fix the two already-found bugs
(`tick_feed` flush gap, missing India-side reconcile notification) as part of
ORD-03/ORD-09 rather than treating them as newly-discovered scope creep — they
are literally what CONCERNS.md predicted would be found here.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Order placement (entry/exit) | API / Backend (`index_ai/dhan_orders.py`, `crypto/executor.py`) | — | Broker-facing HTTP calls, all server-side; no browser/SSR involvement |
| Order cancel (new, ORD-01) | API / Backend | — | Must share the same lock/section as placement; broker-facing |
| Position reconciliation | API / Backend (`index_ai/reconcile.py`, `crypto/executor.reconcile`) | Database / Storage (SQLite journal, `crypto_state.json`) | Compares broker state to the local journal; journal is the storage tier being corrected |
| Websocket tick feed | API / Backend (`index_ai/tick_feed.py`, asyncio background task) | — | A server-side long-lived connection to Dhan, not exposed to the browser directly |
| Tick-driven vs. candle-based stop fallback | API / Backend (`index_ai/scanner.py`) | — | Pure server-side decision logic; no client involvement |
| Reconciliation Telegram alert | API / Backend (`index_ai/notify.py`) | External Service (Telegram Bot API) | Outbound notification, not a UI concern |
| Tick-status dashboard indicator (D-10) | Browser / Client (`dashboard/src/components/shell/StatusPills.tsx`) | API / Backend (`/api/tick-feed`, already exists) | Read-only display of backend state already exposed; no new backend work needed |
| Raw broker-traffic capture (D-03 infra) | API / Backend | Database / Storage (new capture file/table) | Wraps the two REST clients' calls; persists for replay in tests |

## Standard Stack

No new external packages are required for this phase.

### Already available, verified in-repo
| Library | Version (verified) | Purpose | Source |
|---------|---------|---------|--------------|
| `websockets` | 16.0 (installed, confirmed via `python -m pip show websockets`) [VERIFIED: local environment `pip show`] | Underlies `tick_feed.run_feed`'s `websockets.connect` | Already imported in `index_ai/tick_feed.py:193`; **not declared in `pyproject.toml`'s `dependencies`** — worth a one-line fix while this file is being touched, but not a phase blocker since it's already installed |
| `pytest` | `>=8` [VERIFIED: pyproject.toml:29] | Test runner | `pyproject.toml` `[project.optional-dependencies].dev` |
| `httpx` | `>=0.27` [VERIFIED: pyproject.toml:12] | Both `DhanClient` and `DeltaClient`'s synchronous HTTP transport | `pyproject.toml` |

### Explicitly NOT recommended
| Considered | Why not |
|------------|---------|
| `hypothesis` (property-based testing) | CONCERNS.md's own suggestion, but D-03 already locked the approach as **replaying real recorded broker traffic with injected faults**, not generated property-based inputs — a fixed fault-injection harness over real captured payloads satisfies D-03 directly; `hypothesis` would be solving a different problem (input-space exploration) this phase doesn't need. Not installed; do not add it. |
| `pytest-asyncio` | Every existing async test in this codebase (`test_scan_health_reconcile.py`, `test_tick_driven_stops.py`) drives coroutines with a bare `asyncio.run(main())` inside a sync `def test_*`, with no plugin. Introducing `pytest-asyncio` would fragment the test style for no functional gain — follow the existing pattern. |
| A mocking library (`responses`, `respx`, `pytest-httpx`) | Existing tests (e.g. `crypto/executor.py`'s own `__main__` self-check, `test_dhan_orders.py`) pass a hand-written class with the methods under test stubbed — no HTTP-layer mocking library is used anywhere in this codebase. Match that pattern: a small `FakeDhanClient`/`FakeDeltaClient` class per test module (or one shared fixture — see Don't Hand-Roll below), not a new dependency. |

**Installation:** none required.

## Package Legitimacy Audit

Not applicable — no new external packages are installed by this phase's work.
`websockets` is already an installed, working dependency (only its
`pyproject.toml` declaration is missing, a pre-existing gap unrelated to this
phase's scope).

## Critical Findings (read before planning)

### 1. CONTEXT.md file-pointer corrections
- The Dhan **websocket** client is `index_ai/tick_feed.py`
  [VERIFIED: index_ai/tick_feed.py:1-26, 181-263 — module docstring: `"Dhan
  live market feed — real exchange ticks over websocket... wss://api-feed.dhan.co"`],
  **not** `index_ai/dhan.py`. `dhan.py`'s `DhanClient` is REST-only — its
  full method list is `get_order`, `list_today_orders`, `basket_margin`,
  `get_fund_limits`, `list_today_trades`, `trades_for_order`, `list_positions`,
  `ltp`, `index_ltp`, `ohlc`, `intraday_history`, `historical_daily`,
  `expiry_list`, `option_chain`, `place_market_order`
  [VERIFIED: index_ai/dhan.py:79-391 — class `DhanClient` method definitions].
  No `import websockets` anywhere in `dhan.py`; `tick_feed.py:193` has the only
  `import websockets` in `index_ai/`.
- The "reconcile functions" CONTEXT.md attributes to `index_ai/learning.py`
  are actually in `index_ai/reconcile.py` (net-position drift:
  `reconcile()`, `diff_positions()`, `expected_positions()`) and
  `index_ai/dhan_orders.py` (`sync_trade_broker_status()`,
  `sync_open_live_trades()`, order-status polling)
  [VERIFIED: index_ai/reconcile.py:1-184 full file read; index_ai/dhan_orders.py:826-1053].
  `learning.py` supplies only the journal CRUD helpers both modules import
  (`open_trades_for_mode`, `update_trade_status`, `reject_live_trade`,
  `reopen_live_traded_trade`) — it is not itself where the reconcile logic
  lives.

### 2. No cancel-order function exists for either broker (drives ORD-01 scope)
Exhaustive grep for `cancel` (case-insensitive) across `index_ai/` and
`crypto/` found zero broker-cancel code — only `asyncio.Task.cancel()` calls
in `server.py` (task lifecycle) and `scanner.py` (scan-loop teardown)
[VERIFIED: grep `cancel` across index_ai/ and crypto/ — 4 files matched,
all `asyncio.Task.cancel()`/`CancelledError`, none broker-order-related —
confirmed by reading `index_ai/scanner.py:1151,1159` and `server.py:411-420`
directly]. `DhanClient` has no `cancel_order` method
[VERIFIED: index_ai/dhan.py:79-391, full class read]. `DeltaClient` has no
cancel wrapper either [VERIFIED: crypto/delta/client.py:78-265, full class
read — methods are `get_public`, `signed`, `wallet`, `positions`, `fills`,
`margin_required` only]. The real Delta cancel contract (for reference,
should the planner choose to wire it) is visible in the vendored SDK:
`DELETE /v2/orders` with body `{"id": <order_id>, "product_id": <product_id>}`
[CITED: reference/openalgo/broker/deltaexchange/api/order_api.py:590-598].
**Conclusion for the planner:** ORD-01 cannot be "add a lock around existing
cancel logic" — a cancel path must be designed from scratch for at least one
broker (Dhan has no cancel endpoint wrapper either — Dhan's REST API does
support `DELETE /orders/{order-id}`, this just isn't wrapped in `DhanClient`
today), and the race-safety requirement (no duplicate, no orphan) should reuse
`execution_safety._instrument_lock`/`acquire_execution_lock`
[VERIFIED: index_ai/execution_safety.py:25-26,59-65,590-592 — per-instrument
`threading.Lock`, already used by `executor.execute_plan`'s `with lock:`
block at index_ai/executor.py:209,213] as the same critical section a cancel
must acquire, rather than inventing a second lock primitive.

### 3. tick_feed.py has a real, fixable data-loss bug (ORD-03 target)
In `run_feed`'s reconnect loop [VERIFIED: index_ai/tick_feed.py:216-263,
quoted below], the tick buffer is only flushed after the inner `while` loop
exits normally:
```python
while not (stop and stop.is_set()):
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=STALL_SECONDS)
    except asyncio.TimeoutError:
        _state.last_error = "no data — reconnecting"
        break
    ...
    if time.monotonic() - last_flush >= FLUSH_SECONDS or len(buffer) >= 500:
        _state.flushed += await _flush(buffer, sec_map)
        last_flush = time.monotonic()
await _flush(buffer, sec_map)   # <-- only reached via the TimeoutError break, or `stop`
```
If `ws.recv()` raises anything other than `asyncio.TimeoutError` (e.g. a
`websockets.ConnectionClosed` from a real network drop mid-scan-cycle), that
exception propagates past the inner loop, past the `async with
websockets.connect(...)` block (closing the socket), straight to the outer
`except Exception as exc:` handler at line 255 — **skipping the final
`await _flush(buffer, sec_map)` entirely.** Up to `FLUSH_SECONDS=5` seconds'
worth of ticks (or up to 500, whichever triggers first) that were already
received and buffered are lost, not merely delayed. This is precisely the
scenario ORD-03's success criterion names ("does not silently drop tick data
a stop-trigger depends on"). The fix is small (flush in a `finally:` around
the inner loop, or in the outer `except`), and the existing reconnect/backoff
machinery (`STALL_SECONDS=90`, exponential `backoff` capped at `MAX_BACKOFF=60`)
[VERIFIED: index_ai/tick_feed.py:48-50] does not need to change.

### 4. D-03: no raw broker-traffic recorder exists today
`market_log.py` defines exactly four tables: `observations`, `decisions`,
`ticks`, `chain` [VERIFIED: index_ai/market_log.py — `CREATE TABLE IF NOT
EXISTS` at lines 75, 95, 109, 132 for `observations`, `decisions`, `ticks`,
`chain` respectively]. `ticks` stores decoded tick-feed packets (via
`record_tick_batch`, the same function `tick_feed._flush` calls) — already a
*decoded* tick, not a raw order-placement response or raw websocket frame.
`chain` is the option-chain snapshot CLAUDE.md and CONCERNS.md already
describe as a different, known thing. Neither table (nor any other file
found) captures a raw Dhan order-API response or raw Delta order-API
response for later replay. **This confirms D-03's premise: the capture
mechanism must be built this phase**, not reused from existing
infrastructure. Given no traffic-replay library is installed or warranted
(see Standard Stack), the minimal version is a JSONL append of
`(timestamp, broker, endpoint, request, response)` wrapped around the
handful of mutating/status calls each client already makes
(`place_market_order`, `get_order`, `list_today_orders`,
`list_today_trades`, `trades_for_order`, `list_positions` for Dhan;
`signed()` for Delta) — small enough to add without a new dependency, and
fault-injection tests can then replay captured rows with a forced exception
or delay injected at a chosen point.

### 5. ORD-04's fallback already mostly works — three-tier cadence confirmed
[VERIFIED: index_ai/scanner.py:440-620, 991, 1044-1060 — full read]:
1. **Tick-driven (instant):** `on_index_tick` (scanner.py:470) moves the
   trail on every tick and calls `_trigger_tick_check()` the moment a stop is
   crossed — only active when `ENABLE_TICK_FEED=true` and the feed is
   connected and flowing.
2. **Fast candle-based loop, every `TRAIL_FAST_SECONDS = 20` seconds**
   [VERIFIED: index_ai/scanner.py:60,1047-1060] — runs unconditionally
   whenever the market is open, independent of the tick feed's state. This
   IS the "candle-based fallback" ORD-04 refers to, and it already runs
   regardless of tick-feed health.
3. **Full scan cycle's "trails" stage, every `SCAN_INTERVAL_SECONDS = 90`
   seconds** [VERIFIED: index_ai/scanner.py:48,991] — includes a Supertrend
   refresh the faster loops skip.
Because layer 2 runs unconditionally, a stalled or dropped tick feed already
degrades gracefully to a worst-case 20-second stop check — the mechanism
ORD-04 asks for already exists. What's missing, and what this phase should
actually deliver for ORD-04, is: (a) a test that explicitly stalls/disables
the tick feed and proves the 20s loop still closes a crossed-stop position
in the existing test style (see `tests/test_tick_driven_stops.py` for the
pattern), (b) the documentation (D-10, guides/Strategy Guide.md-style), and
(c) the dashboard indicator (see Finding 6).

### 6. D-10: the backend endpoint already exists — only the dashboard wiring is missing
`GET /api/tick-feed` already returns `tick_feed.status()` (`enabled`,
`connected`, `ticks`, `stalled`, `reconnects`, `seconds_since_last_tick`,
`last_error`) [VERIFIED: index_ai/server.py:1465-1470]. Grepping the entire
`dashboard/src` tree for `tick-feed`/`tick_feed` found zero matches outside
`server.py` itself and the (unrelated) `ExchangesView.tsx`'s
`dhan_order_ip_whitelist` — **the dashboard never calls this endpoint today.**
The natural, reusable home is `dashboard/src/components/shell/StatusPills.tsx`
[VERIFIED: full file read] — a small `memo`'d component already rendering a
row of `<Pill>` chips (IST clock, market open/closed, trading mode, "Dhan OK"/
"—") fed from `/api/status`'s response, consumed from `App.tsx:211-217` as
`topRight={<StatusPills tradingMode=... market=... dhanReady=... />}`. Adding
one more `<Pill tone={stalled ? 'warn' : connected ? 'good' : 'idle'}>` fed by
a new `useQuery(['tick-feed'], () => api('/api/tick-feed'))` (or piggybacked
onto the existing `/api/status` poll if the planner prefers one fewer request)
follows the exact established pattern — no new component type, no backend
change needed.

## Findings for D-08 (crypto stuck-position timeout)

Crypto's scan cadence is **60 seconds**
[VERIFIED: index_ai/server.py:215-239 — `_crypto_paper_loop`'s `while True:`
body ends with `await asyncio.sleep(60)`, quoted at server.py:239]. Every
scan, `crypto/lanes.py` calls `executor.reconcile(client)` (line 406)
[VERIFIED: grep `reconcile(client)` in crypto/lanes.py:406] which is
read-only/report-only, and `_reap_exchange_close` (lines 692-720) which only
acts when `executor.position_state(client, sym)` explicitly returns
`"flat"` [VERIFIED: crypto/executor.py:166-175 — `position_state`'s own
docstring: `"'unknown' means the API call failed — the caller must NOT treat
that as flat."`]. **Gap confirmed:** when Delta is unreachable, `position_state`
returns `"unknown"`, `_reap_exchange_close` takes no action (its guard
`if executor.position_state(...) != "flat": return False` is true for
`"unknown"` too), and nothing currently escalates — the position can sit in
this state indefinitely across scan cycles with no forced re-check or alert.
This is exactly the gap D-08 describes. A timeout of roughly **3-5x the scan
cadence (~3-5 minutes)** is proportionate: short enough that "for hours"
(Richard's stated concern) cannot happen, long enough that a single transient
`DeltaError` on one scan doesn't trip it — this is Claude's discretion per
CONTEXT.md; document the exact value and rationale explicitly in the plan
rather than leaving it implicit, per D-07's framing for adjacent
fallback-of-fallback decisions.

## Findings for D-06/D-07 (order-status tie-breaker)

- **Dhan** already exposes what's needed: `GET /orders/{id}` via
  `DhanClient.get_order()`, plus `GET /orders` (order book, `list_today_orders`)
  and `GET /trades` / `GET /trades/{id}` (fill confirmation, `list_today_trades`,
  `trades_for_order`) [VERIFIED: index_ai/dhan.py:198-264]. `dhan_orders.py`'s
  `fetch_order_status()` (lines 154-223) already implements a
  book-then-trades-then-single-lookup cascade — this is the existing,
  reusable tie-breaker primitive for Dhan; ORD-01/ORD-02 should call into it
  rather than duplicating polling logic.
- **Delta** is thinner: `DeltaClient` has no per-order status method at all
  today (only `positions()`, `fills()`, `wallet()`). The vendored reference
  shows Delta's real order-history endpoint is `GET /v2/orders/history`
  [CITED: reference/openalgo/broker/deltaexchange/api/order_api.py:180-181],
  and a single order's fills can already be read via `DeltaClient.signed("GET",
  "/v2/fills")` filtered by `order_id` — `crypto/executor.py`'s existing
  `fill_report()`/`fill_price()` (lines 178-218) already do exactly this
  filtering. **Conclusion:** Delta's order-status tie-breaker is NOT a
  ready-made single call the way Dhan's is — the planner should either wrap
  `GET /v2/orders/history` as a new `DeltaClient` method, or (simpler, reuses
  existing code) build the tie-breaker out of `executor.position_state()` +
  `executor.fill_report()`, which together already answer "is there an open
  position" and "did this specific order fill" without a new endpoint
  wrapper. Given D-02 prioritizes crypto first, this gap is the first piece
  of real (not just test-harness) code this phase needs to add.

## Findings for D-09 (reconciliation Telegram notification)

The reusable pattern is `notify.alert(text, *, key, window_s=3600.0)`
[VERIFIED: index_ai/notify.py:159-162 — `"A one-off operational alert (kill
switch, reconcile, live-order failure), de-duped per key so a stuck loop
cannot spam it."`], which wraps the generic `send()` with de-duplication.
**Crypto already does this correctly**: `crypto/executor.reconcile()` calls
`notify.alert("⚠️ <b>CRYPTO RECONCILE</b>\n" + ..., key="reconcile")` on any
drift [VERIFIED: crypto/executor.py:250-257]. **India does not**:
`index_ai/reconcile.py`'s `reconcile()` has no `notify` import or call
anywhere in the file [VERIFIED: full file read, index_ai/reconcile.py:1-184],
and its caller `scanner._run_reconcile` only logs (`_log("reconcile_drift",
...)`) [VERIFIED: index_ai/scanner.py:421-435], never notifies. **This is a
concrete, pre-existing gap for ORD-02/D-09 to close**, not new design: add
the same `notify.alert(..., key=f"reconcile:{security_id}")` call (per-issue
key so a persistent single-security drift doesn't spam, mirroring crypto's
per-symbol dedup shape) inside `index_ai/reconcile.py::reconcile()` or its
caller, matching crypto's already-shipped pattern exactly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Entry/cancel race lock | A new lock type or global mutex | `execution_safety._instrument_lock()` / `acquire_execution_lock()` (existing per-instrument `threading.Lock`) | Already the exact primitive `executor.execute_plan` uses for entry; a cancel path racing entry must acquire the *same* lock object to actually prevent the race, not a separate one |
| Order-status tie-break (Dhan) | A new polling/cascade function | `dhan_orders.fetch_order_status()` (book → trades → single lookup cascade, already exists) | Already implements exactly the "ask the broker to break the tie" behavior D-06 requires |
| Broker-traffic fault injection | A generative/property-based fuzzing library (`hypothesis`) | A small JSONL capture wrapper + a hand-written `FakeDhanClient`/`FakeDeltaClient` replaying captured rows with an injected exception/delay at a chosen index | D-03 asks for *real recorded traffic* replay, not generated inputs; matches the existing test style (no mocking library used anywhere in this repo today) |
| Crypto position-state polling | A new "is it really open" check | `crypto/executor.position_state()` (already distinguishes `open`/`flat`/`unknown`, already documents the unknown-is-not-flat rule) | The correctness property D-08 needs (don't silently treat unreachable as flat) is already encoded here — just add the timeout/escalation on top |
| Tick-feed dashboard indicator | A new status panel / new API endpoint | `StatusPills.tsx` + existing `GET /api/tick-feed` | Endpoint and component pattern both already exist and are unused together — wiring, not building |

**Key insight:** every one of this phase's four requirements already has at
least one existing, correctly-shaped primitive to extend (a lock, a status
cascade, a position-state check, a status endpoint) — the work is mostly
closing gaps between primitives that already exist rather than inventing new
architecture. The one genuine net-new piece is the cancel path (Finding 2)
and the raw-traffic capture (Finding 4).

## Common Pitfalls

### Pitfall 1: Testing the fast-trail fallback without disabling the tick feed
**What goes wrong:** A test that asserts "the candle-based fallback still
works" but leaves `ENABLE_TICK_FEED` unset/true and never actually stalls
`tick_feed._state` will pass trivially without exercising the fallback path.
**Why it happens:** `_fast_trail_loop` runs unconditionally regardless of
tick-feed state, so it's easy to write a test that happens to pass through
the fast loop without proving the *tick path was actually down*.
**How to avoid:** Follow `tests/test_tick_driven_stops.py`'s pattern —
monkeypatch `scanner._tick_stops`/`_tick_extremes`/`_sec_to_key` directly
rather than running the real feed, and assert the closure happened via the
`_check_trails` call chain with `refresh_supertrend=False`, not via the tick
path.
**Warning signs:** A new test for ORD-04 that never touches `tick_feed._state`
or monkeypatches `tick_feed.enabled`.

### Pitfall 2: Building a new lock for cancel instead of sharing the entry lock
**What goes wrong:** A naive ORD-01 fix adds its own lock around a new
cancel function, distinct from `execution_safety.acquire_execution_lock`. Two
separate locks cannot prevent a race between the two operations they don't
share.
**Why it happens:** `acquire_execution_lock` is currently only imported by
`executor.py`; it's easy to miss when writing new cancel code in a different
module.
**How to avoid:** Import and acquire the exact same
`execution_safety.acquire_execution_lock(instrument_key)` (or the equivalent
trade-scoped lock, if the planner decides cancel needs finer granularity than
per-instrument) from wherever the cancel path lives.
**Warning signs:** A new `threading.Lock()` instantiated outside
`execution_safety.py`.

### Pitfall 3: Treating Delta's `position_state() == "unknown"` as a third case elsewhere
**What goes wrong:** New D-08 escalation code re-derives "is the broker
unreachable" instead of reusing the already-documented `"unknown"` return
value, and ends up inconsistent with `_reap_exchange_close`'s existing
handling.
**Why it happens:** `position_state()`'s unknown-vs-flat distinction is easy
to overlook since Python has no enum here — it's a plain string.
**How to avoid:** Any new D-08 code should branch on the same three string
values `"open" | "flat" | "unknown"` this function already returns, not
introduce a parallel reachability check.
**Warning signs:** A new `try/except DeltaError` around a raw
`client.positions()` call in `crypto/lanes.py` that doesn't go through
`executor.position_state()`.

### Pitfall 4: Money-path async handlers doing blocking I/O
**What goes wrong:** New cancel/reconcile/capture code called from an
`async def` FastAPI handler does a synchronous SQLite write, `.env` write, or
blocking HTTP call directly, stalling the event loop (CLAUDE.md: "This has
bitten twice").
**Why it happens:** Both `DhanClient` and `DeltaClient` are synchronous
(`httpx.Client`, not `httpx.AsyncClient`) by design
[VERIFIED: index_ai/dhan.py:124 `with httpx.Client(timeout=25) as client:`;
crypto/delta/client.py:89-105 `httpx.Client`] — any new code calling them
from an `async def` context must wrap in `asyncio.to_thread`, matching every
existing call site (e.g. `scanner._scan_index` at scanner.py:696,
`server.py:984`).
**How to avoid:** Any new cancel/status-check call added to an async handler
goes through `asyncio.to_thread(...)`, never called bare.
**Warning signs:** A new `client.cancel_order(...)` or `client.get_order(...)`
call directly inside an `async def` function body without `to_thread`.

## Code Examples

### Existing entry-lock pattern to extend for cancel (ORD-01)
```python
# Source: index_ai/executor.py:208-227 (read this session)
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

### Existing order-status cascade to reuse for the Dhan tie-breaker (D-06)
```python
# Source: index_ai/dhan_orders.py:154-223 (read this session) — signature only
def fetch_order_status(
    client: DhanClient,
    order_id: str,
    *,
    book_index: dict[str, dict[str, Any]] | None = None,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read latest status from order book, trade book, then GET /orders/{id}."""
```

### Existing crypto reconcile + notify pattern to mirror for India (D-09)
```python
# Source: crypto/executor.py:223-258 (read this session)
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
`index_ai/reconcile.py::reconcile()` has the equivalent `issues` list already
— it just never calls `notify.alert` on it. Adding the call is the entire
D-09 change for the India side.

### Existing StatusPills pattern to extend for D-10
```tsx
// Source: dashboard/src/components/shell/StatusPills.tsx (read this session, full file)
<Pill tone={dhanReady ? 'good' : 'warn'} title="Dhan broker connection">
  Dhan {dhanReady ? 'OK' : '—'}
</Pill>
// A new tick-status pill follows this exact shape, fed by GET /api/tick-feed's
// `connected`/`stalled`/`enabled` fields instead of `/api/status`'s `dhan_ready`.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| N/A — this is an internal-only hardening phase | N/A | — | No external ecosystem shift applies; all findings are about this codebase's own existing (and missing) code |

**Deprecated/outdated:** None identified — no dependency in this phase's
scope is stale or superseded.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | ~3-5x crypto's 60s scan cadence (~3-5 minutes) is a proportionate D-08 stuck-position timeout | Findings for D-08 | If too short, a single transient Delta API hiccup could trigger unnecessary escalation/alerting; if too long, defeats the "not for hours" intent. This is explicitly flagged as Claude's discretion in CONTEXT.md, not a verified external constraint — confirm the exact value with Richard during planning if precision matters, though CONTEXT.md treats the exact number as delegated. |
| A2 | Dhan's REST API supports `DELETE /orders/{order-id}` for cancel (referenced in Finding 2's "Conclusion for the planner") | Critical Finding 2 | This is stated from general Dhan v2 API knowledge, not confirmed by reading Dhan's official API docs or a Context7 lookup this session (no web/docs tool was available/used in this research pass) — the planner/implementer should verify the exact Dhan cancel endpoint path and payload shape against Dhan's official API reference before implementing, the same way `build_market_order_payload` was clearly built against Dhan's real v2 order-placement contract. |

**If this table is empty:** N/A — two items above need confirmation.

## Open Questions

1. **Should the cancel-race fix be built for Dhan only, Delta only, or both in Phase 2's ORD-01 task?**
   - What we know: D-01 puts both brokers in scope for the whole phase; D-02
     says crypto goes first. Neither locked decision says ORD-01 specifically
     must cover both brokers within the same task, or whether Dhan's cancel
     (needed regardless, since Dhan also has no cancel wrapper) should be a
     separate task.
   - What's unclear: Whether the planner should sequence "build Delta cancel"
     before "build Dhan cancel" as two ORD-01 sub-tasks (matching D-02's
     crypto-first framing) or treat ORD-01 as one cross-broker task.
   - Recommendation: Split into two waves/tasks per broker, crypto first, so
     D-02's priority is honored at the task-sequencing level, not just
     implied by phase-level ordering.

2. **Where should the raw-broker-traffic capture (D-03 infra) live?**
   - What we know: No existing table/file does this; `market_log.py`'s
     SQLite schema is the closest existing pattern (it already tracks
     tick/decision data), but a new JSONL file (matching `crypto/journal.py`'s
     JSONL pattern) is simpler and avoids a schema migration.
   - What's unclear: Whether the planner wants this capture to run
     continuously in production (useful for future debugging beyond this
     phase) or only exist inside the test suite (captured once, checked into
     `tests/fixtures/`, never touching production code paths).
   - Recommendation: Given D-03 only requires *replaying* real traffic for
     tests (not a live production feature), the leanest option is a one-time
     manual/scripted capture checked into `tests/fixtures/broker_traffic/` —
     avoids adding any new always-on capture code to the money path at all,
     which is the lowest-risk option for a phase whose whole point is not
     destabilizing that path.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `websockets` | ORD-03 (tick_feed.py's real dependency) | Yes [VERIFIED: `pip show websockets`] | 16.0 | — |
| `pytest` | All new fault-injection tests | Yes [VERIFIED: pyproject.toml:29] | `>=8` | — |
| Dhan live API access | Only if a real (non-replayed) integration smoke test is desired | Not verified this session (no live credential check performed — `.env` is permission-blocked per CLAUDE.md) | — | D-03's own design means tests run against captured/replayed traffic, not a live Dhan connection — no live credential is actually required for this phase's test suite |
| Delta live API access | Same as above | Not verified this session | — | Same fallback — replayed traffic, not live |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** Live broker API access for either
Dhan or Delta is not required for this phase's own test suite given D-03's
replay-based design; if the planner adds any smoke test against the real
(paper-mode) API, that test should be clearly marked skippable when
credentials are absent, matching how `.env`-gated tests already behave
elsewhere in this suite (session's `conftest.py` deliberately isolates every
test from the real `.env`/DB).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest `>=8` [VERIFIED: pyproject.toml:29] |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `norecursedirs = ["reference"]` [VERIFIED: pyproject.toml:44-46] |
| Quick run command | `python -m pytest tests/test_reconcile.py tests/test_dhan_orders.py tests/test_tick_feed.py tests/test_tick_driven_stops.py tests/test_crypto_phase*.py -x` (adjust to the actual new/touched test files) |
| Full suite command | `python -m pytest -q` (660 tests, ~7-8 min per CLAUDE.md — keep it green) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ORD-01 | Cancel issued mid-placement produces neither a duplicate nor an orphaned order | unit | `pytest tests/test_order_cancel_race.py -x` (new file) | ❌ Wave 0 — no cancel code exists yet to test |
| ORD-02 | Position reconciled correctly after simulated broker disconnection | unit | `pytest tests/test_scan_health_reconcile.py -x` (extend) or a new `tests/test_reconcile_fault_injection.py` | Partial — `diff_positions`/`expected_positions` unit-tested already (`tests/test_scan_health_reconcile.py`); disconnect-simulation cases are ❌ Wave 0 |
| ORD-03 | Websocket reconnect mid-scan-cycle (20+ concurrent candles) does not silently drop tick data | unit/integration | `pytest tests/test_tick_feed.py -x` (extend with an async `run_feed` fault-injection test) | Partial — only `parse_packet`/`status`/`enabled` are tested today; `run_feed`'s reconnect/flush path is ❌ Wave 0 |
| ORD-04 | Tick-driven stop degrades to candle fallback visibly, documented | unit + doc | `pytest tests/test_tick_driven_stops.py tests/test_fast_trail_loop.py -x` (extend) | Partial — tick-crossing mechanics already tested; an explicit "tick feed stalled, 20s loop still closes" test is ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** the relevant file(s) from the table above, `-x`.
- **Per wave merge:** `python -m pytest -q` (full suite).
- **Phase gate:** Full suite green before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] A reusable fake-client test helper for `DhanClient`/`DeltaClient` fault
      injection — no shared fixture exists project-wide today (every test
      file hand-rolls its own stub class); worth a small
      `tests/_fake_brokers.py` helper module given four requirements all need
      one, rather than four near-duplicate inline classes.
- [ ] `tests/fixtures/broker_traffic/` (or equivalent) — the D-03 captured
      real-traffic payloads the fault-injection tests replay. Must exist
      before ORD-01/02/03 tests can be written per D-03's locked approach.
- [ ] `tests/test_order_cancel_race.py` — new, depends on the cancel code
      itself being designed first (Finding 2).
- [ ] An async fault-injection extension to `tests/test_tick_feed.py` that
      drives `run_feed` against a fake `websockets.connect` (monkeypatched,
      matching this repo's existing no-mocking-library style) that raises a
      non-`TimeoutError` exception mid-stream, to pin the Finding 3 bug and
      its fix.

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V1 Architecture | Yes | No new trust boundary is introduced — all work stays server-side behind the existing admin-secret/dashboard-password gates (CLAUDE.md, CONCERNS.md "Authentication gap"); do not add a new unauthenticated endpoint |
| V4 Access Control | No (n/a) | This phase touches no new API surface requiring auth decisions — the new `/api/tick-feed` consumer is read-only dashboard wiring of an already-public-within-the-existing-auth-perimeter endpoint |
| V5 Input Validation | Yes | Broker responses (Dhan order/position rows, Delta fills/positions) are external, untrusted-shape JSON — `normalize_order_response`/`effective_order_status` already defensively parse with `.get()` fallbacks and try/except around `int()`/`float()` coercions [VERIFIED: index_ai/dhan_orders.py:83-107, 428-437]; any new cancel-response or capture-replay parsing must follow the same defensive pattern, never assume a field is present |
| V7 Error Handling and Logging | Yes | Every broker call in this codebase already wraps in try/except with a friendly-error log, never raising raw exceptions into the scan loop (`_log("scan_error", ...)` pattern in scanner.py); new cancel/reconcile code must match this — a hard crash in the money path is worse than a logged, contained failure |
| V9 Communications | Yes (inherited, not new) | Dhan and Delta calls already go over HTTPS/WSS; no plaintext broker traffic. The new D-03 capture must not log secrets (access tokens, API keys) into the persisted traffic file — headers must be redacted before capture, since `DhanClient._headers()` includes `access-token` and `DeltaClient.signed()` includes `api-key`/`signature` |
| V13 API and Web Service | No (n/a) | No new externally-reachable API endpoint is added; `/api/tick-feed` already exists and is unchanged by this phase |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Duplicate live order from a retried/raced cancel | Repudiation / Tampering (financial) | The per-instrument lock (Finding 2/Pitfall 2) — cancel and placement must never interleave without holding the same lock |
| Secret leakage via the new D-03 traffic capture | Information Disclosure | Redact `access-token` (Dhan) and `api-key`/`signature` (Delta) from any persisted request/response before writing to `tests/fixtures/broker_traffic/` or any capture file; never capture raw headers verbatim |
| Silent fund-affecting drift left unnotified | Repudiation | D-09's fix (this phase) — every detected reconciliation issue must reach `notify.alert`, matching crypto's already-correct behavior |

## Sources

### Primary (HIGH confidence — all files read directly this session)
- `index_ai/dhan.py` — full file (413 lines) — DhanClient method surface, confirmed no websocket/cancel
- `index_ai/dhan_orders.py` — full file (1053 lines) — order placement, status cascade, reconcile-adjacent sync functions
- `index_ai/reconcile.py` — full file (184 lines) — net-position drift detection, no notify call
- `index_ai/execution_safety.py` — full file (592 lines) — pre-trade gates, the existing per-instrument lock
- `index_ai/executor.py` — full file (351 lines) — `build_execution_plan`/`execute_plan`, lock usage pattern
- `index_ai/tick_feed.py` — full file (296 lines) — websocket client, the flush-gap bug
- `index_ai/scanner.py` — lines 1-460ish and 440-1070 (tick stops, `_check_trails`, `_fast_trail_loop`, scan cadence constants)
- `index_ai/notify.py` — lines 150-330 (alert/trade_opened/trade_closed/day_report/pre_open patterns)
- `crypto/executor.py` — full file (304 lines) — Delta order placement, kill switch, reconcile+notify pattern
- `crypto/delta/client.py` — full file (266 lines) — DeltaClient method surface, confirmed no cancel/order-status
- `crypto/lanes.py` — targeted reads (scan cadence caller, `_reap_exchange_close`, `_close_live` exit-failure handling)
- `index_ai/server.py` — targeted reads (crypto loop cadence, `/api/tick-feed`, reconcile scheduling)
- `index_ai/market_log.py` — targeted reads (table schema — confirms no raw-traffic capture)
- `dashboard/src/components/shell/StatusPills.tsx` — full file — reusable indicator pattern
- `dashboard/src/App.tsx` — targeted reads — StatusPills wiring point
- `tests/test_scan_health_reconcile.py`, `tests/test_dhan_orders.py`, `tests/test_tick_feed.py`, `tests/test_tick_driven_stops.py`, `tests/conftest.py` — existing test-style patterns
- `pyproject.toml` — dependency list, pytest config, confirmed no hypothesis/pytest-asyncio/mocking library
- `.planning/codebase/CONCERNS.md`, `.planning/phases/02-order-placing-tracking/02-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/config.json` — phase scope and constraints

### Secondary (MEDIUM confidence)
- `reference/openalgo/broker/deltaexchange/api/order_api.py` — vendored reference, cited for Delta's real cancel (`DELETE /v2/orders`) and order-history (`GET /v2/orders/history`) endpoint shapes, per CLAUDE.md's "check it before implementing any Dhan [or Delta] protocol detail" instruction

### Tertiary (LOW confidence)
- Assumption A2 (Dhan's `DELETE /orders/{order-id}` cancel contract) — from general knowledge of the Dhan v2 API, not verified against Dhan's official docs this session (no web/docs tool used); flagged in Assumptions Log for confirmation during implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages needed; all claims verified by reading pyproject.toml and the actual imports
- Architecture: HIGH — every claim about existing code structure is grounded in a full-file or targeted read this session, with file:line citations
- Pitfalls: HIGH — all four pitfalls are drawn directly from patterns observed in the actual codebase (existing lock usage, existing test style, existing position_state semantics, existing async/sync boundary)
- Dhan cancel-endpoint shape (Assumption A2): LOW — not verified against official Dhan docs this session

**Research date:** 2026-09-30
**Valid until:** This is an internal-codebase hardening phase with no external dependency churn risk — valid until the underlying code changes (i.e., effectively for the duration of this phase's planning and execution; re-check file:line citations if planning is deferred more than a few weeks past this date, since this is an actively-developed codebase)
