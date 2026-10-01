---
phase: 02-order-placing-tracking
reviewed: 2026-10-01T00:00:00Z
depth: standard
files_reviewed: 27
files_reviewed_list:
  - crypto/delta/client.py
  - crypto/executor.py
  - crypto/lanes.py
  - dashboard/src/components/shell/StatusPills.tsx
  - guides/Strategy Guide.md
  - index_ai/dhan.py
  - index_ai/dhan_orders.py
  - index_ai/execution_safety.py
  - index_ai/exit.py
  - index_ai/reconcile.py
  - index_ai/server.py
  - index_ai/tick_feed.py
  - scripts/capture_broker_traffic.py
  - tests/_fake_brokers.py
  - tests/fixtures/broker_traffic/delta_rest.jsonl
  - tests/fixtures/broker_traffic/dhan_feed.jsonl
  - tests/fixtures/broker_traffic/dhan_rest.jsonl
  - tests/fixtures/broker_traffic/README.md
  - tests/test_broker_traffic_fixtures.py
  - tests/test_crypto_disconnect.py
  - tests/test_crypto_order_settlement.py
  - tests/test_crypto_phase2.py
  - tests/test_dhan_orders.py
  - tests/test_fast_trail_loop.py
  - tests/test_order_cancel_race.py
  - tests/test_reconcile_fault_injection.py
  - tests/test_tick_feed.py
findings:
  critical: 2
  warning: 4
  info: 3
  total: 9
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-10-01
**Depth:** standard
**Files Reviewed:** 27
**Status:** issues_found

## Summary

The phase's core D-06/D-07 "ask the broker's own book, never resend" pattern is
implemented carefully and symmetrically on both the Dhan and Delta sides for the
*happy* lost-reply path (transport error / timeout → settle via order
book/history → adopt or hold unresolved). The locking discipline (per-instrument
lock, per-trade exit lock, lock-order comments, `wait_for_inflight_entries`) is
well reasoned and mostly correct.

Two real gaps survived review, both directly inside the money/safety path this
phase was supposed to harden:

1. The mid-phase live-incident fix for `btc_daily_straddle` (a position shape
   with no `side` key) only patched the automatic scanner's prune path. The
   manual Close / Close-all path — reachable from the dashboard and from
   `crypto/api.py` right now — was never updated and still crashes with
   `KeyError('side')` on exactly the position shape the incident was about,
   which for "Close all" silently aborts closing every *other* (real,
   possibly live) position queued after it in the same call.
2. Delta's HTTP client decides whether an order's outcome is "unknown" (safe to
   look up, never resend) using an `httpx.DeltaError.status` that is only ever
   set *after* a response body parses as JSON — so a 5xx (or any error status)
   response with a non-JSON body (a gateway/proxy failure, the single most
   likely real-world trigger for "Delta didn't answer") is silently
   misclassified as a definite rejection instead of an unknown outcome,
   defeating the exact protection this phase built.

Four further issues (status-reporting mismatch on an already-closed race, a
narrow barrier gap in `wait_for_inflight_entries`, a stale fixture README, and a
PII leak in a committed fixture) are reported as warnings/info below.

## Critical Issues

### CR-01: Manual crypto Close / Close-all crashes on a `btc_daily_straddle` position

**File:** `crypto/lanes.py:1220`, `crypto/lanes.py:1296`, `crypto/lanes.py:1327-1352`

**Issue:** This phase's own regression test
(`tests/test_crypto_phase2.py::test_prune_removed_strategy_leaves_btc_straddle_slot_alone`)
documents a real production incident: `btc_daily_straddle`'s 2-leg option
position has no `"side"` key, and the generic "prune removed strategies"
cleanup crashed every scan cycle trying to read `pos["side"]`. The fix
(`_known_strategies()` now includes `"btc_daily_straddle"`, `crypto/lanes.py:1118`)
only protects the *automatic scan* prune path.

Two other code paths build the same `"side": pos["side"]` access and were not
touched by the fix:

- `_build_exit_row` (`crypto/lanes.py:1220`) — used by `_close_slot_at_mark`,
  `_reap_exchange_close`, and `_close_position_manual_locked`.
- `_close_position_manual_locked` (`crypto/lanes.py:1296`) — the dashboard's
  manual "Close" button handler (`crypto/api.py:395-403`,
  `crypto_close_position` → `close_position_manual`).

`crypto/api.py`'s `/positions` endpoint (`crypto_positions`, line ~301-313)
deliberately special-cases `btc_straddle.is_straddle_position(p)` so it can
list the straddle row with a `"key": "btc_daily_straddle:BTCUSD"` for the
dashboard — i.e. the dashboard *does* surface a Close control for this
position. Clicking it calls `close_position_manual("btc_daily_straddle:BTCUSD")`
→ `_close_position_manual_locked`, which does:

```python
ev: dict[str, Any] = {
    "strategy": strat,
    "asset": sym,
    "event": "exit",
    "side": pos["side"],   # KeyError — straddle positions have no "side" key
    ...
}
```

This raises `KeyError('side')` with no try/except anywhere in the call chain
(`crypto/api.py`'s route is a plain `def`, not wrapped), so the request 500s.

Worse, `close_all_positions_manual` (`crypto/lanes.py:1327-1352`) builds its
key list from *every* key with an open `position`, straddle included, then
does `results = {k: close_position_manual(k, client) for k in keys}` — a dict
comprehension with no per-key try/except. The docstring claims "a key that
fails ... doesn't stop the rest," but an *unhandled exception* (as opposed to
a handled `{"ok": False, ...}` result) aborts the comprehension entirely. If
the straddle key is iterated before a real/live perp position's key, that
live position's close is never even attempted — "Close all" (the operator's
emergency stop-everything control) silently leaves it open while returning a
500 to the caller.

Today `btc_daily_straddle` is paper-only (per its own module docstring), so
this cannot yet place a duplicate/stuck *live* order by itself — but it can
and will break the "Close all" safety action for every other position
(including live ones) whenever a straddle happens to be open, which is
precisely the scenario this phase's own incident writeup should have made the
team check for.

**Fix:** Give `_close_position_manual_locked` / `close_all_positions_manual`
/ `_build_exit_row` the same straddle awareness `_known_strategies()` got, e.g.:

```python
def _close_position_manual_locked(key, client):
    strat, _, sym = key.partition(":")
    ...
    pos = slot.get("position")
    if not pos:
        return {"ok": False, "error": f"no open position for {key}"}
    if strat == "btc_daily_straddle":
        return {"ok": False, "error": "manual close for btc_daily_straddle is not implemented yet"}
    ...
```

and in `close_all_positions_manual`, wrap the per-key call so one failure
(crash or handled error) truly cannot stop the rest:

```python
def _safe_close(k):
    try:
        return close_position_manual(k, client)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

results = {k: _safe_close(k) for k in keys}
```

### CR-02: Delta client misclassifies a non-JSON 5xx response as a definite (not unknown) order outcome

**File:** `crypto/delta/client.py:245-269`

**Issue:** `outcome_unknown()` (`crypto/executor.py:230-238`) is the gate that
decides whether a failed order placement is looked up on Delta's own book
(never resent) versus treated as a definite rejection:

```python
def outcome_unknown(exc: Exception) -> bool:
    if not isinstance(exc, DeltaError):
        return False
    if isinstance(exc.__cause__, httpx.TransportError):
        return True
    return exc.status is not None and exc.status >= 500
```

`exc.status` is only populated in one place in `DeltaClient._request` — the
branch guarded by `if resp.status_code >= 400 or ...` at `crypto/delta/client.py:258-269`.
But that branch is reached *only after* `resp.json()` has already succeeded
(`crypto/delta/client.py:251-256`):

```python
try:
    data = resp.json()
except ValueError as exc:
    raise DeltaError(
        f"{m} {path}: non-JSON response (HTTP {resp.status_code})"
    ) from exc          # <-- no status=, no httpx.TransportError cause

if resp.status_code >= 400 or (...):
    raise DeltaError(..., status=resp.status_code)   # status set here only
```

If a signed `POST /v2/orders` (or `DELETE /v2/orders`, or the leverage call)
gets back a 5xx with a non-JSON body — the single most realistic shape for a
gateway/proxy failure sitting in front of Delta's API, i.e. exactly the kind
of transient infra blip this phase's D-06/D-07 pattern exists to handle — the
raised `DeltaError` has `status=None` and `__cause__` is a `ValueError`
(from the JSON decode), not an `httpx.TransportError`. `outcome_unknown()`
then returns `False`.

Trace the consequence in `crypto/lanes.py:_apply_entry` (~line 1003-1016):

```python
except Exception as exc:
    reject_reason = str(exc)
    if executor.outcome_unknown(exc):
        settle = executor.settle_entry(...)   # never reached for this case
        ...
    if resp is None:
        new_state["position"] = None  # order failed -> we are flat, record nothing
```

Because `outcome_unknown` wrongly says "definite," the code skips
`settle_entry` entirely and records the order as failed/flat. If the 5xx
gateway response actually followed a successful order placement on Delta's
side (plausible for a reverse-proxy timeout after the backend processed the
request), the system now believes it holds no position while Delta actually
holds one — an orphaned live position with no local stop-loss, trail, or exit
management, tracked nowhere.

Compare `index_ai/dhan.py`: Dhan's client calls `response.raise_for_status()`
(which raises `httpx.HTTPStatusError`, giving `order_outcome_unknown` a real
status to check) **before** attempting `response.json()` — so a non-JSON 5xx
body on the Dhan side is correctly classified as unknown. The two broker
clients are asymmetric on exactly the scenario this phase's hint #4 asks to
check, and Delta is the one that's wrong.

**Fix:** Set `status=resp.status_code` on the non-JSON branch too (or reorder
to check `resp.status_code >= 400` before attempting `resp.json()`, matching
Dhan's client):

```python
try:
    data = resp.json()
except ValueError as exc:
    raise DeltaError(
        f"{m} {path}: non-JSON response (HTTP {resp.status_code})",
        status=resp.status_code,
    ) from exc
```

## Warnings

### WR-01: `close_open_trade`'s new settle_pending_entry hook mislabels an already-closed race as "BLOCKED"

**File:** `index_ai/exit.py:203-227`

**Issue:** The new pre-lock settle block:

```python
settled = settle_pending_entry(client, trade, settings=app_settings)
settled_status = str(settled.get("status") or "")
if settled_status == "CANCELLED":
    return {"status": "CANCELLED", "trade_id": trade_id, "reason": settled.get("reason")}
if settled_status == "LIVE_TRADED":
    trade = settled.get("trade") or trade
else:
    return {"status": "BLOCKED", "trade_id": trade_id, "reason": settled.get("reason")}
```

`settle_pending_entry` (`index_ai/dhan_orders.py:1289-1448`) can legitimately
return `{"status": "ALREADY_CLOSED", ...}` — either when the trade id isn't
found (`dhan_orders.py:1329`) or, more importantly, when it re-reads the row
fresh under the instrument lock and finds `pnl` already set
(`dhan_orders.py:1332`, the exact race this lock exists to catch: a
concurrent close already won). That status falls into the `else` branch here
and is returned to the caller as `"BLOCKED"`.

`index_ai/server.py:_close_all_trades_sync` only treats
`{"CLOSED", "ALREADY_CLOSED", "CANCELLED"}` as success
(`index_ai/server.py:1945-1949`); `"BLOCKED"` is not in that set. So a trade
that was *actually already closed* (by a concurrent trailing-stop sweep, a
double-click, etc.) gets reported as a Close-all failure even though nothing
is wrong — `result["ok"]` goes `False` and the trade shows up in `failed`,
which could prompt an operator to retry a close that already succeeded, or
to believe a position is stuck when it isn't. No test exercises this branch
(`tests/test_order_cancel_race.py` covers the `LIVE_TRADED`-after-stale-read
race and the `CANCELLED` race, but not `settle_pending_entry` returning
`ALREADY_CLOSED`).

**Fix:** Pass through `ALREADY_CLOSED` (and in general, any status that isn't
itself an in-progress/failed state) instead of folding it into `BLOCKED`:

```python
if settled_status in {"CANCELLED", "ALREADY_CLOSED"}:
    return {"status": settled_status, "trade_id": trade_id, "reason": settled.get("reason")}
if settled_status == "LIVE_TRADED":
    trade = settled.get("trade") or trade
else:
    return {"status": "BLOCKED", "trade_id": trade_id, "reason": settled.get("reason")}
```

### WR-02: `wait_for_inflight_entries` can miss an instrument's very first in-flight entry

**File:** `index_ai/execution_safety.py:594-609`

**Issue:** The ORD-01 barrier snapshots only the locks that already exist:

```python
def wait_for_inflight_entries() -> None:
    with _GLOBAL_EXECUTE_LOCK:
        locks = list(_INSTRUMENT_LOCKS.values())
    for lock in locks:
        with lock:
            pass
```

`_INSTRUMENT_LOCKS` entries are created lazily, the first time
`acquire_execution_lock(instrument_key)` is called for that instrument
(`execution_safety.py:59-65`). If `wait_for_inflight_entries()` runs at the
exact moment an instrument is being traded for the first time in the
process's lifetime — `execute_plan` hasn't yet called `_instrument_lock` for
it — the snapshot taken here won't include that instrument's lock at all, so
the barrier returns immediately without waiting for that in-flight entry to
finish journalling. `_close_all_trades_sync`'s subsequent `open_trades()`
read could then miss the trade being placed concurrently, which is exactly
the scenario this barrier was added to close (per its own docstring/ORD-01
comment). Narrow (only the first trade per instrument per process run) but
real, and not covered by `test_wait_for_inflight_entries_blocks_while_lock_held`
(which pre-creates the lock before testing the barrier).

**Fix:** Pre-create locks for all known instruments at startup
(`for key in ("NIFTY", "BANKNIFTY", "SENSEX"): acquire_execution_lock(key)`),
or have `acquire_execution_lock` register new instrument keys through a path
`wait_for_inflight_entries` is guaranteed to observe before `execute_plan`
can begin placing against that lock.

### WR-03: `tests/fixtures/broker_traffic/README.md` documents a file the capture script doesn't produce

**File:** `tests/fixtures/broker_traffic/README.md:15`

**Issue:** The file table lists `dhan_journal_orders.jsonl` as a separate
fixture file ("Real order rows extracted from the app's own SQLite journal").
`scripts/capture_broker_traffic.py:capture_dhan()` (lines 253-291) actually
merges `_dhan_journal_rows()` straight into `dhan_rest.jsonl` — there is no
separate `dhan_journal_orders.jsonl` file, and none is committed. A developer
following this README to find journal-sourced rows will look for a file that
doesn't exist; they're mixed into `dhan_rest.jsonl` (rows with
`"source": "journal"`).

**Fix:** Update the table to drop the `dhan_journal_orders.jsonl` row, or
note under `dhan_rest.jsonl` that it also carries `"source": "journal"` rows.

### WR-04: Committed Delta fixture leaks a real public IP address

**File:** `tests/fixtures/broker_traffic/delta_rest.jsonl:1-5`

**Issue:** Every captured row's error body includes
`"context": {"client_ip": "223.236.99.27"}` — Richard's own real public/
residential IP, captured verbatim from Delta's `ip_not_whitelisted_for_api_key`
error. `scripts/capture_broker_traffic.py`'s `REDACT_KEYS` set
(`capture_broker_traffic.py:36-60`) does not include `client_ip` (or
`ip`/`clientip`), and `redact()`'s secret-substring check only matches the
configured API key/secret strings, not IP addresses — so this isn't flagged
by `_secret_leaked()` and isn't a credential the review's "no API
key/secret/token/signature" check is scoped to. It is still PII (an
identifiable home/ISP address) committed to version control, which is a
quieter but real instance of the general secret-hygiene goal this phase's
own fixture tooling was built to enforce.

**Fix:** Add `client_ip` / `ip` to `REDACT_KEYS`, or special-case the
`DeltaError.code == "ip_not_whitelisted_for_api_key"` body shape in `redact()`
so re-captures don't reintroduce it; re-capture (or hand-edit) the existing
`delta_rest.jsonl` to replace the real IP with a placeholder.

## Info

### IN-01: `on_tick` exceptions now surface via `_state.last_error`, overwriting a possibly more useful prior error

**File:** `index_ai/tick_feed.py:282-289`

**Issue:** Minor, but worth noting for the dashboard-facing claim in the
Strategy Guide update (`on_tick_errors ... failures inside the stop-trigger
handler itself"): each `on_tick` failure now overwrites `_state.last_error`
with the exception text, and since `last_error` is deliberately *not* reset
on a successful reconnect (per the new comment at `tick_feed.py:238-241`), a
transient `on_tick` exception can permanently mask a more informative earlier
connection-level error (or vice versa) in the `/api/tick-feed` status until
the next real failure. Not observable on the dashboard pill itself (it only
reads `connected`/`stalled`/`enabled`), only in the raw `GET /api/tick-feed`
payload referenced by the new guide section. Low impact; flagging since the
guide now tells the operator to look at this exact field when debugging.

### IN-02: Strategy Guide overstates the no-tick-loss guarantee on disconnect

**File:** `guides/Strategy Guide.md` (new "Stop checks" section, ~line 197)

**Issue:** The guide states: "any ticks it did receive before a drop are
still saved to the tick log — nothing already received is lost." The code's
own comment at `index_ai/tick_feed.py:256-261` documents the real ceiling
more precisely: "a second cancellation during that last write can still lose
it (ponytail: acceptable ceiling — at most FLUSH_SECONDS of tick log...)".
The guide's "nothing already received is lost" is a small overstatement of
what the code actually guarantees; not user-facing-critical (this is a
research/ops doc, not a trading claim), but worth tightening so a future
reader doesn't treat the buffer as bulletproof.

**Fix:** Soften to something like "...are flushed to the tick log before
reconnecting — a hard shutdown mid-flush can still lose the last few
seconds, see `tick_feed.py`'s own comment for the exact ceiling."

---

_Reviewed: 2026-10-01_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
