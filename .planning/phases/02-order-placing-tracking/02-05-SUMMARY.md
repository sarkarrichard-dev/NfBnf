---
phase: 02-order-placing-tracking
plan: 05
subsystem: india-options
tags: [dhan, reconcile, fault-injection, notify, testing]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking
    plan: 03
    provides: "strict= switch on build_order_book_index/build_trade_fill_index/build_position_index (default off), fake_dhan_client, tests/fixtures/broker_traffic/dhan_rest.jsonl -- this plan turns strict on where a wrong 'empty' answer causes harm"
  - phase: 02-order-placing-tracking
    plan: 04
    provides: "settle_pending_entry's own strict=True reads, acquire_execution_lock reused unchanged by this plan's per-trade sync lock"
provides:
  - "sync_open_live_trades: lists open live trades before any broker call, builds all three Dhan indexes with strict=True inside one try -- a disconnect aborts the whole pass before touching a single row instead of reading a swallowed error as 'Dhan shows nothing' (ORD-02, D-07)"
  - "sync_open_live_trades processes each trade under acquire_execution_lock(instrument) -- no new lock"
  - "reconcile(): strict position read distinguishes 'Dhan unreachable' from 'every position gone'; the early no-open-live-trades return is removed so an untracked Dhan position (ORPHAN_BROKER) is caught even when nothing is open"
  - "reconcile(): one plain-language notify.alert per drift issue (GHOST_JOURNAL/ORPHAN_BROKER/UNDER_FILLED/OVER_FILLED) and one on an unreadable read, keyed per security id and kind for the existing hourly de-dup (D-09)"
affects: [02-06, 02-07]

# Actuals (#2632)
actuals:
  tokens: 8752
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "List trades before any broker call, then read strictly inside one try -- a failed read aborts the whole sync/reconcile pass before any journal write, the same 'strict read first, abort on failure' shape 02-03/02-04 established for settle_pending_entry, now applied to the two remaining read paths that used to swallow a broker outage into an empty result"
    - "Per-security, per-kind alert keys (f'reconcile:{sid}:{kind}') feeding notify.alert's existing hourly de-dup window -- mirrors crypto/executor.py's reconcile alert pattern (notify.alert with a stable key, wrapped in try/except so a Telegram failure never breaks reconcile)"

key-files:
  created:
    - tests/test_reconcile_fault_injection.py
  modified:
    - index_ai/dhan_orders.py
    - index_ai/reconcile.py
    - tests/test_dhan_orders.py

key-decisions:
  - "sync_open_live_trades builds all three indexes (order book, trade book, positions) once per sync batch, strictly, BEFORE acquiring any per-instrument lock; the per-trade lock only wraps the processing step (sync_trade_broker_status, position verification, the reject/close decision) -- matches the plan's own lock-order note (instrument then exit, never held while reading the shared indexes) and keeps a slow/faulting broker read from serializing behind an unrelated instrument's lock"
  - "The unreachable-sync alert counts only trades with pnl is None (genuinely still-open rows) toward its 'N open live trade(s)' wording, not today's already-zeroed LIVE_REJECTED rows that live_trades_for_broker_sync() also returns for same-day re-check -- a false reject that's already been zeroed isn't 'open' in the sense the alert means"
  - "reconcile()'s alert-per-issue loop fires for every issue kind uniformly (GHOST_JOURNAL/ORPHAN_BROKER/UNDER_FILLED/OVER_FILLED) rather than only the two the plan's <behavior> block called out by name (GHOST_JOURNAL, ORPHAN_BROKER) -- the plan's action text explicitly lists plain wording for all four kinds, and under/over-filled are exactly as actionable as the other two, so alerting only a subset would silently under-report real drift"
  - "_leg_label falls back to the bare security id when no journal leg claims it (the ORPHAN_BROKER-with-nothing-tracked case) rather than raising or omitting the alert -- the alert must still fire even with the thinnest possible identifying information"

requirements-completed: [ORD-02]

coverage:
  - id: D1
    description: "During a simulated Dhan disconnect, the live sync leaves every open live India trade exactly as it was (no false reject, no false close, no duplicate row) and sends one alert naming how many open live trades could not be confirmed, for both a young and an aged trade"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_disconnect_young_trade_unchanged_one_alert"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_disconnect_aged_trade_unchanged_no_exit"
        status: pass
    human_judgment: false
  - id: D2
    description: "On reconnect, a consistent book/trade/position read leaves a LIVE_TRADED row unchanged with no alert; a genuinely gone position (past the grace window) is closed exactly once via close_open_trade(skip_broker_exit=True), and a second sync afterward makes no broker call at all"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconnect_consistent_stays_live_traded_no_alert"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconnect_position_genuinely_gone_closes_once"
        status: pass
    human_judgment: false
  - id: D3
    description: "No duplicate or lost row across a disconnect/reconnect/reconnect sequence; a sync with no open live trades makes no broker call at all; a sync of a NIFTY row waits for another caller's held instrument lock and completes only after release"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_sync_never_inserts_or_loses_a_row"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_no_live_trades_makes_no_broker_call"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_sync_waits_for_instrument_lock"
        status: pass
    human_judgment: false
  - id: D4
    description: "reconcile() tells 'Dhan is down' from 'position gone': an unreadable strict position read returns ok=False with an error starting 'could not read broker positions', empty issues/repaired, one alert only when a live row is actually open (quiet when nothing is open), and RECONCILE_AUTO_REPAIR never fires on this path"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_disconnect_with_open_row_blocks_and_alerts"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_disconnect_no_open_rows_is_quiet"
        status: pass
    human_judgment: false
  - id: D5
    description: "Every drift kind (GHOST_JOURNAL, ORPHAN_BROKER including with no open journal rows at all, UNDER_FILLED, OVER_FILLED) reports and alerts with its own plain wording, naming the index/strike/type when a journal leg identifies it; GHOST_JOURNAL still auto-repairs (journal-only) when RECONCILE_AUTO_REPAIR is on; reconcile never places or cancels a broker order"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_ghost_journal_alerts_with_leg_label_and_repairs"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_orphan_with_no_open_rows_alerts"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_under_and_over_filled_each_alert"
        status: pass
      - kind: other
        ref: "grep -nE \"place_market_order|cancel_order|place_live_\" index_ai/reconcile.py (no matches)"
        status: pass
    human_judgment: false
  - id: D6
    description: "Alerts are keyed per security id and kind (stable across repeated calls, feeding notify.alert's existing hourly de-dup); a Telegram failure inside the alert never breaks reconcile; paper mode still returns skipped with no broker call and no alert"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_alert_key_stable_across_repeated_calls"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_telegram_failure_is_swallowed"
        status: pass
      - kind: integration
        ref: "tests/test_reconcile_fault_injection.py#test_reconcile_paper_mode_no_broker_call_no_alert"
        status: pass
    human_judgment: false

duration: 38min
completed: 2026-10-01
status: complete
---

# Phase 2 Plan 5: India Broker-Disconnect Hardening + Reconcile Alerts Summary

**`sync_open_live_trades` now lists trades before any broker call and reads Dhan's order book/trade book/positions with `strict=True` inside one try, so a disconnect aborts the whole pass before touching a single journal row instead of a swallowed exception reading as "Dhan shows nothing" and falsely rejecting or closing a real open position — and `reconcile()` gained the same strict read plus a plain-language, per-security/per-kind Telegram alert on every drift it finds, mirroring crypto's existing reconcile alert pattern.**

## Performance

- **Duration:** ~38 min
- **Started:** 2026-10-01T04:20:00+05:30 (approx., following 02-04's completion commit)
- **Completed:** 2026-10-01T04:58:00+05:30 (approx.)
- **Tasks:** 2
- **Files modified:** 3 production/test files modified, 1 new test file

## Accomplishments

- `index_ai/dhan_orders.py`: `sync_open_live_trades` lists open live trades first (returns 0 before any broker call when there are none), then builds all three Dhan indexes with `strict=True` inside one try. An unreadable read logs a warning and, only when at least one listed trade is still genuinely open (`pnl is None`), sends one `notify.alert` naming how many trades could not be confirmed (key `reconcile:unreachable`) — no row is read-modify-written on this path. Each trade is then processed under `acquire_execution_lock(instrument)`, the same per-instrument lock `execute_plan`/`settle_pending_entry` already hold — no new lock.
- `index_ai/reconcile.py`: `reconcile()` reads positions with `strict=True` and keeps going instead of returning early when no live trade is open, so an untracked Dhan position (a hedge left from a failed spread entry, say) is now caught as `ORPHAN_BROKER` even with nothing in the journal. A failed strict read returns `ok=False` with an error starting `"could not read broker positions"` and alerts once only when a live row is actually open — the existing journal-only `GHOST_JOURNAL` auto-repair never fires on that path. Every issue `diff_positions` finds now sends one plain-language `notify.alert`, keyed per security id and kind for the existing hourly de-dup, naming the index/strike/type from the matching journal leg when one exists.
- `tests/test_reconcile_fault_injection.py`: new fault-injection test module — Task 1's tests replay real, redacted, recorded Dhan traffic (`tests/_fake_brokers.py`, D-03) through the real `DhanClient`/`dhan_orders` code with injected transport faults; Task 2's tests use a minimal fake positions client (reconcile only ever calls `list_positions()`) since exercising the full `DhanClient`/`BrokerReplay` stack adds no value for testing `reconcile.py` itself.

## Task Commits

1. **Task 1: The live sync survives a Dhan disconnect without touching a single row** — `312ea9e` (feat, TDD: two disconnect tests confirmed failing against the pre-change `sync_open_live_trades` before implementation — see TDD Gate Compliance below)
2. **Task 2: Reconciliation reads Dhan strictly, catches untracked positions with no open rows, and tells Richard in plain words** — `57b79bf` (feat, TDD: disconnect-with-repair and orphan-with-no-rows tests confirmed failing against the pre-change `reconcile()` before implementation)

**Plan metadata:** (this commit)

## Files Created/Modified

- `index_ai/dhan_orders.py` — `_alert_sync_unreachable`, `sync_open_live_trades` rewritten (list-first, strict indexes, per-trade instrument lock)
- `index_ai/reconcile.py` — `_ISSUE_WORDING`, `_leg_label`, `_alert_issue`, `_alert_unreadable`, `reconcile()` rewritten (strict read, no early empty-trades return, per-issue alerting)
- `tests/test_dhan_orders.py` — the three builder-monkeypatch lambdas accept and ignore the `strict` keyword (12 lines changed, per plan's own acceptance criterion)
- `tests/test_reconcile_fault_injection.py` — 15 new tests across both tasks (7 Task 1, 8 Task 2)

## Decisions Made

See `key-decisions` in frontmatter. Summarized: the three Dhan indexes are built once per sync batch (strictly) before any instrument lock is taken, with the per-trade lock scoped only to the processing/decision step; the unreachable-sync alert counts only genuinely-open rows (`pnl is None`), not today's already-zeroed false rejects; `reconcile()`'s alert loop fires for all four drift kinds uniformly, not just the two the `<behavior>` block named by example, since the action text specifies wording for all four and under/over-filled are equally actionable; `_leg_label` falls back to the bare security id rather than skipping the alert when no journal leg identifies a position.

## Deviations from Plan

None — plan executed exactly as written. The two "Flagged Assumptions" the plan itself surfaced (ORD-02's "no stale or duplicate" reading, and the orphan-alert-for-manual-trades tradeoff) were both deliberate, pre-acknowledged plan decisions, not deviations discovered during execution — see the plan's own "Flagged Assumptions" section.

## TDD Gate Compliance

Both tasks carry `tdd="true"`. Per-task gate evidence:

- **Task 1:** `test_disconnect_young_trade_unchanged_one_alert` and `test_disconnect_aged_trade_unchanged_no_exit` were written first and run against the pre-change `sync_open_live_trades` (via `git stash push -- index_ai/dhan_orders.py`) — both failed with `assert 1 == 0` (the pre-change code updated the row instead of leaving it untouched during a simulated disconnect). After the implementation, both pass; all 68 tests in the Task 1 verify set pass.
- **Task 2:** `test_reconcile_disconnect_with_open_row_blocks_and_alerts` and `test_reconcile_orphan_with_no_open_rows_alerts` were written first and run against the pre-change `reconcile()` (via `git stash push -- index_ai/reconcile.py`) — the first failed with `KeyError: 'error'` (the pre-change code never set that key on a disconnected read), the second failed because `reconcile()` returned the old `"skipped": "no open live trades"` result instead of reporting the orphan. After the implementation, both pass; all 54 tests in the Task 2 verify set pass.

No gate violations.

## Reviewer Findings (trading-safety-reviewer, performed manually, no Task/subagent-spawning tool available in this execution environment, same as 02-03/02-04)

Followed `.claude/agents/trading-safety-reviewer.md`'s checklist directly against `git diff` of `index_ai/dhan_orders.py` and `index_ai/reconcile.py`:

- **Blocking I/O on the event loop:** N/A — no `async def` handler touched by this plan's diff (`grep -n "^async def\|    async def" index_ai/reconcile.py index_ai/dhan_orders.py` — no matches). `scanner._run_reconcile` still calls `reconcile` via `asyncio.to_thread`, unchanged.
- **Read-modify-write races:** `sync_open_live_trades` reuses the existing `acquire_execution_lock` (no new primitive); `grep -rn "threading.Lock()\|threading.RLock()" index_ai crypto --include=*.py \| wc -l` still prints 12. `reconcile.py`'s alert loop reads issues already computed and has no shared mutable state of its own.
- **Live-arming interlock:** untouched — `grep -n "arm_live_trading|set_trading_mode|update_env_values|set_feature_flag" index_ai/reconcile.py index_ai/dhan_orders.py` has no matches.
- **Cost-model shortcuts:** N/A, `charges.py`/`spread_calib.py` untouched by this plan.
- **Order sequencing / partial fills:** N/A — no order placement code touched; both changes are read-then-report, and `reconcile.py` still never places or cancels an order (`grep -nE "place_market_order|cancel_order|place_live_" index_ai/reconcile.py` — no matches).
- Also re-ran `tests/test_live_arming.py tests/test_scan_health_reconcile.py` (the reviewer's own suggested regression check) — 30 passed.
- Clean — no findings requiring a fix.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. No new env keys (`RECONCILE_AUTO_REPAIR` already existed and keeps its default).

## Next Phase Readiness

- The full suite is 754 passed (up from 739 at the end of 02-04 by exactly the 15 tests this plan added); `ruff check index_ai/` still shows the same 7 pre-existing errors, 0 new.
- ORD-02 is now fully closed — this was the last plan in the phase declaring it (confirmed via `requirements.ready-ids` before marking complete).
- No blockers.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-10-01*

## Self-Check: PASSED

All created/modified files verified present on disk; both task commits (`312ea9e`, `57b79bf`) verified present in `git log`; full suite (754 tests) and `ruff check index_ai/` (7 pre-existing errors, 0 new) verified green in this session.
