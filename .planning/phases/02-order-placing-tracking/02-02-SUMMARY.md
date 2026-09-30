---
phase: 02-order-placing-tracking
plan: 02
subsystem: crypto
tags: [delta-exchange, threading, outage-handling, reconcile, testing]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking
    provides: "02-01's unclear_entry marker, _resolve_unclear_entry, settle_entry (D-06/D-07), tests/_fake_brokers.py (BrokerReplay, Block, fake_delta_client, setup_live_crypto_lane)"
provides:
  - "ORD-01: close_all_positions_manual / close_position_manual can no longer miss a crypto position that was mid-placement (inside POST /v2/orders) the instant Close-all was pressed — keys are read under _STATE_LOCK"
  - "ORD-02: a Delta outage on a held live position is never read as flat — unknown_since tracking in _reap_exchange_close, held across scans, reaped exactly once on reconnect"
  - "D-08/D-09: a plain-language Telegram alert after UNCLEAR_ALERT_SECONDS (180s) for a stuck-unknown position or a stuck-unconfirmed entry, de-duped hourly"
  - "crypto reconcile is retried on the next scan (not marked done for the day) when it can't read Delta, with one alert when local live positions exist to confirm"
affects: [02-03, 02-04, 02-05]

# Actuals (#2632)
actuals:
  tokens: 5836
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "position_state's three real answers (open/flat/unknown) are branched on individually rather than collapsed to a boolean — the unknown branch now does real work (persist + escalate) instead of being a silent no-op"
    - "Race tests hold a real network call open via tests/_fake_brokers.py's Block, run the two competing code paths on real threads, and always release/join in a finally block so a failing assertion can never leave a background thread mutating monkeypatched state after the test (and the next test's fixtures) have torn down"

key-files:
  created:
    - tests/test_crypto_disconnect.py
  modified:
    - crypto/lanes.py
    - crypto/executor.py

key-decisions:
  - "UNCLEAR_ALERT_SECONDS = 180 (three 60s scans) — Claude's discretion per the plan, a fixed module constant rather than an env key since nothing needs to tune it"
  - "_reap_exchange_close and _resolve_unclear_entry's escalation use separate notify keys (c-unknown:.../c-unclear-stuck:...) so the two kinds of 'stuck' alert never collide or suppress each other"
  - "reconcile()'s new alert only fires when local live positions exist — an unreadable reconcile with nothing live locally has nothing to hide, so it stays silent"

requirements-completed: [ORD-01, ORD-02]

coverage:
  - id: D1
    description: "Close-all (and single Close) wait for an in-flight crypto entry placement before reading which positions are open, so a position being opened the instant Close-all is pressed is still closed"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_close_all_waits_for_inflight_entry_then_closes_it"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_close_position_manual_waits_for_inflight_entry_then_closes_it"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_close_all_nothing_in_flight_behaves_as_before"
        status: pass
    human_judgment: false
  - id: D2
    description: "A Delta outage on a held live position is held (no exit order, no journal close, no alert yet), escalated once after 180s with hourly de-dup, and reconciled exactly once when Delta answers again (still open -> silent continue; flat -> reap once, no duplicate)"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_outage_while_holding_keeps_position_no_exit_no_alert_yet"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_outage_escalates_after_180s_then_deduplicates"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_reconnect_still_open_clears_marker_no_alert"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_reconnect_flat_reaps_once_no_duplicate"
        status: pass
    human_judgment: false
  - id: D3
    description: "A stuck unclear_entry (unconfirmed order, every re-check still failing) escalates once after 180s, still skipped (no resend)"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_unconfirmed_entry_stuck_escalates_once_still_skipped"
        status: pass
    human_judgment: false
  - id: D4
    description: "A daily crypto reconcile that could not read Delta is retried next scan instead of being marked done for the day; one alert fires when local live positions exist to confirm"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_crypto_disconnect.py#test_reconcile_unreadable_not_marked_done_retried_next_scan"
        status: pass
      - kind: unit
        ref: "tests/test_crypto_disconnect.py#test_reconcile_unreadable_no_alert_when_nothing_local_live"
        status: pass
    human_judgment: false

duration: 55min
completed: 2026-09-30
status: complete
---

# Phase 2 Plan 2: Crypto Close-All Race Fix and Delta Outage Survival Summary

**Close-all/close can no longer miss a crypto position mid-placement (reads open keys under _STATE_LOCK), and a Delta outage on a held position is now held-and-escalated (180s, hourly de-dup) instead of being silently ignored or read as closed — proven on replayed real Delta traffic through the real client/executor/lane code.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-09-30T17:25:00Z (approx.)
- **Completed:** 2026-09-30T18:18:27Z
- **Tasks:** 2
- **Files modified:** 3 (1 created, 2 modified)

## Accomplishments

- **Task 1 (ORD-01):** `close_all_positions_manual` now reads the open-position keys inside `with _STATE_LOCK:` before releasing it and closing each key through the existing `close_position_manual` path — this waits for any in-flight scan (including one blocked mid-`POST /v2/orders`), so a position being opened at the exact moment Close-all is pressed is now included. No new lock — reuses `_STATE_LOCK` (`threading.Lock()`/`RLock()` count unchanged at 12). A real-thread race test (holding the entry POST open via `tests/_fake_brokers.Block`) proved the bug first (close-all returned with `attempted: 0` while the entry was still in flight) and then proved the fix.
- **Task 2 (ORD-02, D-08, D-09):** `_reap_exchange_close` now branches on `executor.position_state`'s three real answers instead of collapsing `unknown` into the same "do nothing" path as `open`. `unknown` now persists `pos["unknown_since"]` on first sight and sends one plain-language Telegram alert once 180 seconds (`UNCLEAR_ALERT_SECONDS`, three 60s scans) have passed, de-duped hourly by `notify`'s own key window; `open` clears the marker silently; `flat` still reaps and journals exactly once, unchanged. The identical 180s escalation was added to `_resolve_unclear_entry`'s `UNKNOWN` branch for a stuck unconfirmed entry, with its own distinct alert key. `crypto/executor.py` gained `RECONCILE_UNREADABLE`; `crypto/lanes.py`'s scan now only marks `_live_reconciled = today` when `reconcile()`'s issues don't start with that prefix, so one Delta outage no longer switches off that day's reconcile — it's retried next scan. `reconcile()` itself now sends one alert when local live positions exist and Delta can't be read (silent when nothing local is live). `reconcile` remains report-only in every branch — it never trades to "fix" a mismatch.

## Task Commits

1. **Task 1: Close-all pressed while a crypto entry is being placed closes that position too** - `dce1aa5` (fix)
2. **Task 2: Delta outage — unknown positions held, escalated after 180s, reconciled once Delta answers; reconcile retried** - `0a0c292` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `tests/test_crypto_disconnect.py` — 10 tests: 3 for the Close-all/Close race (Task 1), 7 for the outage/escalation/reconcile-retry behaviors (Task 2)
- `crypto/lanes.py` — `UNCLEAR_ALERT_SECONDS` constant; `close_all_positions_manual`'s keys now read under `_STATE_LOCK`; `_reap_exchange_close` branches on open/flat/unknown with `unknown_since` tracking and escalation; `_resolve_unclear_entry`'s `UNKNOWN` branch escalates after 180s; the `_live_reconciled` write is now conditional on a readable reconcile
- `crypto/executor.py` — `RECONCILE_UNREADABLE` constant; `reconcile()` sends one alert on an unreadable run when local live positions exist

## Decisions Made

See `key-decisions` in frontmatter — summarized: `UNCLEAR_ALERT_SECONDS = 180` (three 60s scans, Claude's discretion per the plan); the two escalation paths (held-unknown-position vs. stuck-unclear-entry) use distinct notify keys so they never suppress each other; `reconcile()`'s new alert is silent when nothing local is live (nothing to hide).

## Deviations from Plan

None in the code itself — plan executed exactly as written, both tasks' `<acceptance_criteria>` verified (grep checks for `UNCLEAR_ALERT_SECONDS`, `RECONCILE_UNREADABLE`, the third `with _STATE_LOCK` use, and the unchanged lock count of 12 all pass).

### Auto-fixed Issues

None — no Rule 1-3 auto-fixes were needed beyond the plan's own scope.

---

**Total deviations:** 0 auto-fixed.
**Impact on plan:** None — see "Issues Encountered" below for a process (not code) deviation worth flagging.

## Issues Encountered

**Commit-boundary mistake (process, not a code defect).** I intended to split `crypto/lanes.py`'s changes across the two task commits using `git add -p` to stage only the `close_all_positions_manual` hunk for Task 1's commit. That staging succeeded, but running `git commit -m "..." -- crypto/lanes.py tests/test_crypto_disconnect.py` does **not** commit only what's staged for those paths — `git commit <pathspec>` commits the full *working-tree* content of the named paths, overriding the index for them. As a result, Task 1's commit (`dce1aa5`) contains all of `crypto/lanes.py`'s changes (both tasks), not just the Close-all fix; Task 2's commit (`0a0c292`) has an empty `crypto/lanes.py` diff and covers only `crypto/executor.py` and the Task 2 test additions. The behavior itself is correct and fully tested either way (confirmed by running the full plan verification suite and the whole repo's test suite — both green — after each commit); this is purely a commit-message/diff-boundary accuracy issue, documented here rather than fixed via `--amend` per the "prefer new commits over amending" rule. Both commit messages cross-reference this.

**Reused, pre-fixed `_live_pos()` test default.** The plan's `FAKE_ENTRY_TS` fixture constant is `2026-09-07`, which is far enough in the past relative to the real system clock (2026-09-30) that seeding a held live position with that as `opened_at` would trip the lane's own unrelated max-hold-days force-close and mask the outage behavior under test. `tests/test_crypto_disconnect.py`'s `_live_pos()` helper defaults `opened_at` to the real current time instead, noted inline in the test file.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `crypto/lanes.py`'s `UNCLEAR_ALERT_SECONDS`, `unknown_since` tracking, and the `_STATE_LOCK`-guarded `close_all_positions_manual` are all in place for plan 02-04 (Dhan's `wait_for_inflight_entries`, the D-01 symmetric design this plan's ORD-01 fix mirrors) to reference as the proven crypto-side pattern.
- `crypto/executor.py`'s `RECONCILE_UNREADABLE` is available for plan 02-05 if the Dhan side needs the same "retry, don't mark done" reconcile pattern.
- No blockers.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-09-30*

## Self-Check: PASSED

All 3 created/modified files verified present on disk; both task commits (`dce1aa5`, `0a0c292`) verified present in `git log`; plan verification suite (`tests/test_crypto_disconnect.py tests/test_crypto_order_settlement.py tests/test_crypto_phase4.py tests/test_crypto_phase2.py`, 52 tests) and the full repo suite (704 tests) both green after the final commit.
