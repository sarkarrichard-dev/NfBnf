---
phase: 02-order-placing-tracking
plan: 04
subsystem: india-options
tags: [dhan, order-cancel, idempotency, threading, testing]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking
    plan: 03
    provides: "DhanClient.cancel_order, DELETE transport branch, order_outcome_unknown/find_order_by_correlation/_place_or_settle, sync_trade_broker_status, fake_dhan_client + dhan_rest.jsonl fixture -- all reused unchanged by this plan"
provides:
  - "settle_pending_entry(client, trade, *, settings) -- cancels a still-working India entry under the per-instrument lock and settles strictly from Dhan's order book (D-06), never trusting the cancel reply"
  - "close_open_trade routes LIVE_SENT/LIVE_PENDING through settle_pending_entry before the per-trade exit lock; new CANCELLED result"
  - "wait_for_inflight_entries() -- a lock-free barrier that lets India Close-all wait for an in-flight entry placement to finish before reading open_trades()"
  - "_close_all_trades_sync counts CANCELLED as a successful close"
affects: [02-05, 02-06, 02-07]

# Actuals (#2632)
actuals:
  tokens: 9925
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cancel-then-reread, never trust-the-cancel-reply (D-06 applied to DELETE, not just POST): a cancel can lose the race to a fill, so only a fresh strict read of Dhan's order book after the cancel attempt decides CANCELLED vs LIVE_TRADED vs UNRESOLVED"
    - "Barrier via existing locks, no new lock: wait_for_inflight_entries snapshots the per-instrument lock dict and acquires/releases each in turn -- reuses execute_plan's own locking instead of adding a second synchronization primitive"

key-files:
  created: []
  modified:
    - index_ai/dhan_orders.py
    - index_ai/exit.py
    - index_ai/execution_safety.py
    - index_ai/server.py
    - tests/test_order_cancel_race.py

key-decisions:
  - "settle_pending_entry's 'every leg rejected/cancelled/never-reached' CANCELLED decision folds unconfirmed correlation ids not found on the book into the same bucket as a cancelled order id, rather than tracking them in a separate always-CANCELLED-only path -- both mean 'nothing is holding a real position on Dhan', which is the only fact settle needs"
  - "The mixed-outcome alert (_alert_settle_partial) only names legs settle_pending_entry itself found TRADED, reusing _alert_legs_possibly_open's wording but a settle-scoped key (settle-partial:{trade_id}) so it doesn't collide with the entry-time partial-fill alert (partial-entry:{base_id}) from plan 02-03"
  - "wait_for_inflight_entries takes a snapshot of _INSTRUMENT_LOCKS.values() under the existing _GLOBAL_EXECUTE_LOCK rather than adding any new lock or counter -- a lock a placement is holding when the snapshot is taken is the only lock that can matter, since any entry started after the snapshot is by definition not the one Close-all needs to wait for"
  - "Deviation from the plan's literal <behavior> text for the 'live orders not enabled' case: verified against the real code (not assumed) that a disarmed/non-Live pending row closes silently today without ever returning BLOCKED -- the plan's prose said 'today's BLOCKED result', the actual pre-existing (and by design untouched) behavior is a silent CLOSED with an estimated price. The safety-relevant assertion -- settle_pending_entry is never called when live orders are disabled -- holds and is tested; see Deviations below."

requirements-completed: [ORD-01]

coverage:
  - id: D1
    description: "A Close/Close-all/stop-loss/15:10 square-off on a live India trade whose entry order is still pending at Dhan cancels the pending order instead of being refused: if the cancel wins, nothing is held, no exit order is sent, and the trade is closed with zero P&L and a reason saying it was cancelled before it filled"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_cancel_wins_row_rejected_with_zero_pnl"
        status: pass
    human_judgment: false
  - id: D2
    description: "If the entry fills before the cancel lands (out-of-order), the cancel's failure is ignored, Dhan's order book is re-read, and the trade is closed with exactly one exit order"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_fill_wins_out_of_order_closes_with_one_exit"
        status: pass
    human_judgment: false
  - id: D3
    description: "If Dhan cannot be asked, or only part of a spread filled, nothing is sent, the trade is left as it is, the close answers BLOCKED with a plain reason, and one Telegram alert names what needs checking on Dhan"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_dhan_unreachable_blocks_nothing_sent"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_partial_spread_cancels_short_alerts_open_hedge"
        status: pass
    human_judgment: false
  - id: D4
    description: "An unconfirmed entry row whose correlation id is not on the now-readable book is cancelled with zero DELETE and zero POST calls"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_unconfirmed_not_on_book_cancels_with_zero_broker_calls"
        status: pass
    human_judgment: false
  - id: D5
    description: "A trade already LIVE_TRADED by the time the lock is acquired runs the normal close (one exit order); skip_broker_exit=True never calls settle_pending_entry; live orders not enabled never calls settle_pending_entry either"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_settle_already_live_traded_runs_normal_close"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_skip_broker_exit_never_calls_settle_pending_entry"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_live_orders_not_enabled_never_calls_settle_pending_entry"
        status: pass
    human_judgment: false
  - id: D6
    description: "Pressing India Close-all while the scanner is mid-placement waits for that placement to finish and then closes (or cancels) the new trade too -- one entry order and at most one exit order for it; CANCELLED counts as a successful close"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_close_all_race_fill_wins_closes_the_entry"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_close_all_race_still_pending_cancels_entry"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_close_all_counts_cancelled_as_success"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_close_all_no_open_trades_is_a_quick_noop"
        status: pass
      - kind: unit
        ref: "tests/test_order_cancel_race.py#test_wait_for_inflight_entries_returns_at_once_when_idle"
        status: pass
      - kind: unit
        ref: "tests/test_order_cancel_race.py#test_wait_for_inflight_entries_blocks_while_lock_held"
        status: pass
    human_judgment: false
  - id: D7
    description: "No new lock is added and the lock order stays acyclic (instrument lock before exit lock, never the reverse); settle_pending_entry runs entirely under acquire_execution_lock, never under the per-trade exit lock"
    requirement: ORD-01
    verification:
      - kind: other
        ref: "grep -rn \"threading.Lock()|threading.RLock()\" index_ai crypto --include=*.py | wc -l  (== 12, unchanged)"
        status: pass
      - kind: manual_procedural
        ref: "Manual trading-safety-reviewer pass over exit.py/dhan_orders.py/execution_safety.py/server.py diff (no subagent tool available this session)"
        status: pass
    human_judgment: false

duration: 22min
completed: 2026-10-01
status: complete
---

# Phase 2 Plan 4: India Cancel-While-Pending + Close-All Race Fix Summary

**`settle_pending_entry` cancels a still-working India entry under the same per-instrument lock `execute_plan` holds, settling strictly from a fresh, strict re-read of Dhan's order book after the cancel attempt (never the DELETE reply itself) — `close_open_trade` now routes `LIVE_SENT`/`LIVE_PENDING` through it before taking the exit lock, and `wait_for_inflight_entries` gives India Close-all a lock-based barrier so a trade being placed at the exact moment Close-all is pressed is no longer silently skipped.**

## Performance

- **Duration:** ~22 min
- **Started:** 2026-10-01T03:29:00+05:30 (approx., following 02-03's completion commit)
- **Completed:** 2026-10-01T03:46:36+05:30
- **Tasks:** 2
- **Files modified:** 4 production (`index_ai/dhan_orders.py`, `index_ai/exit.py`, `index_ai/execution_safety.py`, `index_ai/server.py`) + 1 test file

## Accomplishments

- `index_ai/dhan_orders.py`: `settle_pending_entry(client, trade, *, settings)` — the whole cancel-while-pending flow under `acquire_execution_lock`: re-reads the trade row fresh, reads Dhan's book+fills strictly (unreachable → `UNRESOLVED` + alert), cancels every `PENDING`/`TRANSIT` leg (ignoring the DELETE reply itself), re-reads strictly, and decides `CANCELLED` (every leg rejected/cancelled/never reached Dhan → `reject_live_trade`), `LIVE_TRADED` (every leg confirmed → delegates to `sync_trade_broker_status`), or `UNRESOLVED` (mixed — alerts the filled leg(s), sends nothing).
- `index_ai/exit.py`: `close_open_trade` now settles a `LIVE_SENT`/`LIVE_PENDING` live row *before* taking the per-trade exit lock (documented why: settle takes the instrument lock, and taking the exit lock first would risk a deadlock against `execute_plan`'s instrument-then-nothing order). `CANCELLED` short-circuits with zero P&L; `LIVE_TRADED` swaps in the fresh row and falls through to the existing locked close (one exit order); anything else returns `BLOCKED`.
- `index_ai/execution_safety.py`: `wait_for_inflight_entries()` — a barrier that snapshots the current per-instrument locks and acquires/releases each in turn, creating no new lock. `index_ai/server.py`'s `_close_all_trades_sync` calls it before `open_trades()`, and now counts `CANCELLED` as a successful close alongside `CLOSED`/`ALREADY_CLOSED`.

## Task Commits

1. **Task 1: Closing a live trade whose entry is still pending cancels it, settles from Dhan's book, and exits at most once** — `cf6f1ec` (feat)
2. **Task 2: India Close-all issued while an entry is being placed waits for it and closes it too** — `bfd5441` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `index_ai/dhan_orders.py` — `settle_pending_entry`, `_alert_settle_partial`
- `index_ai/exit.py` — `close_open_trade`'s pre-exit-lock settle route, `CANCELLED` result
- `index_ai/execution_safety.py` — `wait_for_inflight_entries`
- `index_ai/server.py` — `_close_all_trades_sync` calls the barrier, counts `CANCELLED` as success
- `tests/test_order_cancel_race.py` — 14 new tests across both tasks (8 Task 1, 6 Task 2)

## Decisions Made

See `key-decisions` in frontmatter. Summarized: unconfirmed correlation ids not found on the book fold into the same "CANCELLED" bucket as a cancelled order id (both mean nothing real is on Dhan); the mixed-outcome alert reuses `_alert_legs_possibly_open`'s wording under a settle-scoped notify key so it never collides with plan 02-03's entry-time partial alert; `wait_for_inflight_entries` deliberately adds no new lock, reusing the exact per-instrument locks `execute_plan`/`settle_pending_entry` already take; the "live orders not enabled" test asserts the actually-observed current behavior (verified directly against the real code, not assumed from the plan's prose) rather than the plan's literal "BLOCKED" claim — see Deviations.

## Deviations from Plan

### Auto-fixed Issues

None — no Rule 1-3 bugs or missing-critical-functionality found during implementation; both tasks' tests passed against the implementation on the first run.

### Documented Finding (not a code change)

**1. The plan's `<behavior>` text for "Live orders not enabled" does not match the actual current code**
- **Found during:** Task 1, while writing `test_live_orders_not_enabled_never_calls_settle_pending_entry`
- **Plan's claim:** "Live orders not enabled -> today's BLOCKED result, settle_pending_entry never called"
- **What was actually verified:** `_close_open_trade_locked`'s live-order block only ever returns `BLOCKED` from inside `if live_orders_enabled(app_settings):` — when `live_orders_enabled` is `False` (disarmed, or `trading_mode` not `LIVE`), that whole inner block is skipped with **no `else`**, so execution falls straight through to the journal-pnl computation and the row is silently `CLOSED` using an estimated price, never contacting Dhan. This was confirmed experimentally against the real `close_open_trade` (not assumed) before writing the test — and, on the first such probe, that confirmation accidentally wrote a real trade row into `memory/trade_memory.sqlite` (the production journal) because the check ran as a standalone script rather than inside pytest's `tmp_path`-redirected DB fixture. The row (`3aa63172-…`, `status=CLOSED`, no real broker order since the transport was the fake client) was deleted immediately by id once the mistake was noticed; no real Dhan order was ever sent. All actual test code lives inside pytest and never touches the real database.
- **Resolution:** No code change — this is pre-existing, explicitly out-of-scope behavior per the plan's own Flagged Assumptions ("Disarmed while pending: … unchanged"). The test (`test_live_orders_not_enabled_never_calls_settle_pending_entry`) asserts the real, verified outcome (`result["status"] == "CLOSED"`, zero GET/DELETE/POST broker calls, `settle_pending_entry` never invoked) instead of the plan's assumed `BLOCKED`. The safety-relevant guarantee this bullet exists to protect — a disarmed pending row is never routed through the new cancel machinery — is proven and unchanged.
- **Files modified:** none (test-only; documented here per the "surface, don't drop" instruction for flagged assumptions)
- **Verification:** `tests/test_order_cancel_race.py#test_live_orders_not_enabled_never_calls_settle_pending_entry` — pass
- **Committed in:** `cf6f1ec` (Task 1 commit)

---

**Total deviations:** 0 auto-fixed code changes. **1 documented finding** (a plan-prose vs. actual-behavior mismatch in an already-flagged edge case, resolved by testing reality rather than the assumption — no functional change, no scope creep).

## Reviewer Findings (Task 2 — trading-safety-reviewer, performed manually, no subagent tool available this session)

Followed `.claude/agents/trading-safety-reviewer.md`'s checklist directly against `git diff` of `index_ai/exit.py`, `index_ai/dhan_orders.py`, `index_ai/execution_safety.py`, `index_ai/server.py`:

- **Blocking I/O on the event loop:** N/A — `wait_for_inflight_entries` and `settle_pending_entry` are both plain `def`, called only from already-worker-thread contexts (`_close_all_trades_sync`/`_close_trade_by_id_sync`, both still reached via `asyncio.to_thread` from their async handlers, confirmed unchanged at `index_ai/server.py:1977`).
- **Read-modify-write races:** `wait_for_inflight_entries` reads `_INSTRUMENT_LOCKS.values()` under the existing `_GLOBAL_EXECUTE_LOCK` (same guard `_instrument_lock()` uses) — no unguarded read of shared mutable state. `settle_pending_entry` writes only via the existing `learning.*` connection-per-call functions.
- **Live-arming interlock:** untouched. `grep -n "arm_live_trading|set_trading_mode|update_env_values|set_feature_flag"` over the two touched files matches only pre-existing, unrelated call sites outside this plan's diff region. `settle_pending_entry` is only ever reached from `exit.py` behind `live_orders_enabled(app_settings)`, which itself is unchanged.
- **Cost-model shortcuts:** N/A, `charges.py`/`spread_calib.py` untouched by this plan.
- **Order sequencing / partial fills:** `settle_pending_entry` never sends a corrective order for a partial fill — a mixed outcome is always `UNRESOLVED` + alert, matching ORD-01's explicit prohibition (proven by `test_settle_partial_spread_cancels_short_alerts_open_hedge`). `wait_for_order_terminal`/leg sequencing in `dhan_orders.py` unchanged by this plan.
- Also re-ran `tests/test_live_arming.py tests/test_scan_health_reconcile.py` (the reviewer's own suggested regression check) — 30 passed.
- Clean — no findings requiring a fix.

## Issues Encountered

None beyond the documented finding above (plan-prose mismatch, not a bug).

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `settle_pending_entry` and `wait_for_inflight_entries` are both broker-agnostic in shape (mirroring plan 02-02's crypto `settle_entry`/`_STATE_LOCK`-scoped Close-all fix) and are the natural extension points if a future plan needs the same pending-entry-cancel or in-flight-wait behavior for commodities' shared Dhan connection.
- Full suite: 739 passed (up from 725 at the end of 02-03 by exactly the 14 tests this plan added); `ruff check index_ai/` still shows the same 7 pre-existing errors, 0 new.
- No blockers.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-10-01*

## Self-Check: PASSED

All modified files verified present on disk; both task commits (`cf6f1ec`, `bfd5441`) verified present in `git log`; full suite (739 tests) and `ruff check index_ai/` (7 pre-existing errors, 0 new) verified green in this session.
