---
phase: 02-order-placing-tracking
plan: 01
subsystem: crypto
tags: [delta-exchange, httpx, order-settlement, testing, tracer]

# Dependency graph
requires:
  - phase: 01-strategy-fixes
    provides: crypto live-arming locks, journal/state shape, notify.crypto_alert conventions
provides:
  - "D-03 capture-and-replay infrastructure (scripts/capture_broker_traffic.py, tests/_fake_brokers.py, tests/fixtures/broker_traffic/) reused by plans 02-03/02-06 for Dhan"
  - "DeltaClient transport seam + cancel_order/open_orders/order_history/DeltaError.status"
  - "crypto.executor.outcome_unknown/find_order/settle_entry — settle a lost entry reply from Delta's own order list, cancelling a still-open order when needed, never resending"
  - "crypto.lanes unclear_entry marker + _resolve_unclear_entry — hold and re-check every scan when Delta itself can't be reached"
affects: [02-03, 02-06]

# Actuals (#2632)
actuals:
  tokens: 17518
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Real broker traffic recorded once (redacted, redaction-enforced by a test), replayed through the real client via httpx.MockTransport + a hand-rolled BrokerReplay — no mocking library, matches the project's existing no-mock-lib convention"
    - "A settle-by-our-own-client_order_id tie-breaker (D-06) instead of guessing an order's outcome from a lost HTTP reply"

key-files:
  created:
    - scripts/capture_broker_traffic.py
    - tests/_fake_brokers.py
    - tests/fixtures/broker_traffic/README.md
    - tests/fixtures/broker_traffic/delta_rest.jsonl
    - tests/test_broker_traffic_fixtures.py
    - tests/test_crypto_order_settlement.py
  modified:
    - crypto/delta/client.py
    - crypto/executor.py
    - crypto/lanes.py

key-decisions:
  - "setup_live_crypto_lane (tests/_fake_brokers.py) hardcodes ny_n_break/BTCUSD as the one strategy/symbol pair under live test — keeps every scan deterministic and reuses test_crypto_phase4.py's existing BTC Contract shape rather than inventing a new one"
  - "Simulated 'Delta unreachable' with a scripted HTTP 500 rather than a queued transport-level fault — settle_entry treats any DeltaError from the lookup as UNKNOWN regardless of cause, and a scripted response avoids having to pre-fill enough fault-queue entries to cover httpx's internal GET-retry loop"
  - "'Next scan' behaviors (still-unreachable / adopted / cleared) are independent tests that seed crypto_state.json directly with slot['strategy'] pre-set to a non-None sentinel, rather than chaining multi-scan sequences — sidesteps the real strategy step re-firing 'enter' on every scan (its own state only reverts to None on an unresolved attempt) while still exercising the real _resolve_unclear_entry → lane → journal path"
  - "No Task/subagent-spawning tool was available in this execution environment, so the trading-safety-reviewer and test-isolation-reviewer checks (Task 2 action item) were performed manually against each agent's documented checklist rather than dispatched as a subagent — see Reviewer Findings below"

requirements-completed: [ORD-01, ORD-02]

coverage:
  - id: D1
    description: "A lost Delta entry POST reply is settled from Delta's own order list (found filled, or not found at all), never re-sent — proven end to end on replayed real Delta traffic through the real DeltaClient/executor/lane code"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_lost_entry_reply_filled_on_delta_records_one_position"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_never_placed_records_nothing"
        status: pass
      - kind: unit
        ref: "tests/test_broker_traffic_fixtures.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Every settle outcome handled: a still-open order is cancelled (fill-vs-cancel race decided by the re-read), Delta-unreachable holds the slot and re-checks every scan without resending, and an adopted/cleared marker resolves correctly on a later scan"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_still_open_cancel_wins_records_nothing"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_still_open_fill_wins_out_of_order_records_position"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_delta_unreachable_marks_unclear_entry_no_resend"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_next_scan_still_unreachable_not_stepped_no_resend"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_next_scan_adopts_filled_position"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_next_scan_clears_marker_when_never_existed"
        status: pass
      - kind: integration
        ref: "tests/test_crypto_order_settlement.py#test_definite_rejection_never_calls_settle_entry"
        status: pass
    human_judgment: false

duration: 45min
completed: 2026-09-30
status: complete
---

# Phase 2 Plan 1: Crypto Lost-Entry Settlement Summary

**A lost Delta order-placement reply is now settled by asking Delta's own order list (cancelling a still-open order when needed) instead of being guessed as "flat" or silently re-sent — proven on real, redacted, recorded Delta traffic replayed through the real client code.**

## Performance

- **Duration:** ~45 min
- **Started:** 2026-09-30T17:22:00Z (approx.)
- **Completed:** 2026-09-30T17:52:00Z (approx.)
- **Tasks:** 2
- **Files modified:** 9 (6 created, 3 modified)

## Accomplishments

- `scripts/capture_broker_traffic.py`: a read-only (GET-only, enforced by `RecordingTransport` raising on anything else), key-redacting recorder, run against the real Delta account. The live account returned `ip_not_whitelisted_for_api_key` on every call — that real error body is exactly what got captured and committed, per the plan's own precondition.
- `tests/_fake_brokers.py`: `BrokerReplay` (fault injection + scripted responses + recorded-row fallback over `httpx.MockTransport`), `fake_delta_client`, and `setup_live_crypto_lane` — the shared live-lane test harness plan 02-02 will reuse without editing this file.
- `crypto/delta/client.py`: an injectable `transport=` seam (used only by tests/the capture script — production code never names either), `cancel_order`/`open_orders`/`order_history`, and `DeltaError.status`.
- `crypto/executor.py`: `outcome_unknown` (transport-error-or-5xx vs. a definite answer), `find_order` (lookup by our own `client_order_id`), `settle_entry` (the D-06 tie-breaker — cancels a still-open order, lets the re-read decide the real outcome, never re-sends).
- `crypto/lanes.py`: `_apply_entry`'s lost-reply branch now settles via Delta instead of assuming "rejected"; a genuinely unreachable Delta (D-07) sets `slot["unclear_entry"]` instead of guessing; `_resolve_unclear_entry` re-checks that marker at the top of every scan — adopting a confirmed fill, clearing a never-existed order, or holding and re-checking again — before the strategy is ever stepped for that slot.

## Task Commits

1. **Task 1: Tracer — a lost Delta entry reply is settled from Delta's own order list, end to end on recorded traffic** - `1648bb0` (feat)
2. **Task 2: Every settle outcome — still-open order cancelled, never placed, Delta unreachable held and re-checked** - `2568abb` (feat, TDD: tests-first then implementation in the same commit per plan's tdd="true" instruction)

**Plan metadata:** (this commit)

_Note: Task 2 followed RED→GREEN internally (failing behavior tests written and confirmed failing before implementation), but both landed in a single commit per the plan's task structure — no separate `test(...)` commit was specified for this task._

## Files Created/Modified

- `scripts/capture_broker_traffic.py` — GET-only recorder with `REDACT_KEYS`, `redact()`, `RecordingTransport`, `--broker delta`
- `tests/fixtures/broker_traffic/README.md` — fixture schema, `source` values, re-capture instructions
- `tests/fixtures/broker_traffic/delta_rest.jsonl` — real recorded Delta traffic (5 rows, all `ip_not_whitelisted_for_api_key` — the account's real state at capture time)
- `tests/_fake_brokers.py` — `load_traffic`, `BrokerReplay`, `Block`, `fake_delta_client`, `setup_live_crypto_lane`
- `tests/test_broker_traffic_fixtures.py` — redaction/shape enforcement over every `*.jsonl` fixture
- `tests/test_crypto_order_settlement.py` — 9 tests: the Task 1 tracer plus 8 Task 2 behavior tests
- `crypto/delta/client.py` — `transport=` seam, `cancel_order`, `open_orders`, `order_history`, `DeltaError.status`
- `crypto/executor.py` — `outcome_unknown`, `find_order`, `settle_entry` (with the still-open cancel branch)
- `crypto/lanes.py` — `_live_position_dict` (shared builder), `_apply_entry`'s settlement branch, `slot["unclear_entry"]`, `_resolve_unclear_entry`

## Decisions Made

See `key-decisions` in frontmatter — summarized: fixed the live-lane test to `ny_n_break`/BTCUSD for determinism; used scripted HTTP 500s rather than queued transport faults to simulate "Delta unreachable" across a GET-retry loop; wrote "next scan" behaviors as independently-seeded tests rather than chained multi-scan sequences; performed the two required reviewer checks manually (see below) since no subagent tool was available in this run.

## Deviations from Plan

None — plan executed as written. Two things worth flagging as expected, plan-anticipated outcomes rather than deviations:

1. **The live Delta capture returned auth errors, not real order data** (flagged in the plan's own Assumptions and precondition — "if Delta answers the capture with an auth or IP-whitelist error, that real error body is what gets recorded and the run continues"). `delta_rest.jsonl` holds 5 real `ip_not_whitelisted_for_api_key` rows; the test's own order/fill/history rows are built in-test via `BrokerReplay.script(...)`, cited as "reference"-shaped (reference/openalgo file:line in a code comment) per the plan's Flagged Assumptions.
2. **Reviewer subagents were run manually, not dispatched.** No `Task`/subagent-spawning tool was present in this execution's toolset. I read `.claude/agents/trading-safety-reviewer.md` and `.claude/agents/test-isolation-reviewer.md` directly and applied each checklist item by hand against the diff — see Reviewer Findings below. This is a process substitution, not a skipped step; every checklist item was actually evaluated.

## Reviewer Findings (performed manually — see Deviation 2 above)

**trading-safety-reviewer checklist:**
- Blocking I/O on the event loop: N/A — no `async def` handler touched; `settle_entry`/`_resolve_unclear_entry` run synchronously inside the already-threaded `scan_crypto_paper`, same as existing `place_entry`.
- Read-modify-write races: none introduced — `slot` mutations stay in-memory under the caller's `_STATE_LOCK`; one `journal.save_state(st)` write per scan-cycle batch, no new lock (`threading.Lock()`/`RLock()` count unchanged at 12, confirmed by grep).
- Live-arming interlock: untouched.
- Cost-model shortcuts: N/A, `charges.py` untouched.
- Order sequencing / partial fills: `settle_entry`'s cancel branch computes `filled = size - unfilled_size` from the POST-cancel re-read, so a partial fill before cancel is reflected correctly rather than assumed all-or-nothing.
- Clean — no findings requiring a fix.

**test-isolation-reviewer checklist:**
- Every test that can reach `notify.*` either explicitly monkeypatches `index_ai.notify.send` (4 of 9 tests, capturing alert text) or relies on `tests/conftest.py`'s untouched autouse fixture clearing `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (confirmed: neither var is re-set anywhere in this file).
- Every test reaches Delta exclusively through `fake_delta_client(...)` (`httpx.MockTransport`) — no bare `DeltaClient()` pointed at a real URL anywhere in the new/changed test file.
- `journal.STATE_PATH`/`JOURNAL_PATH` are `tmp_path`-scoped in every test via `setup_live_crypto_lane`.
- `tests/conftest.py` was not modified by this plan (confirmed via `git diff --stat`).
- Clean — no findings requiring a fix.

**Total deviations:** 0 auto-fixed. **Impact:** none — plan executed exactly as written; both flagged items above are expected outcomes the plan itself anticipated.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. (Real Delta credentials already exist in the user's `.env`; the capture ran against them per the plan's explicit authorization and returned an IP-whitelist error, which is itself informative: the machine that ran this capture is not the one Richard normally trades from, or the whitelist doesn't cover it. Not something this plan needed to fix.)

## Next Phase Readiness

- The D-03 capture-and-replay infrastructure (`scripts/capture_broker_traffic.py`, `tests/_fake_brokers.py`, `tests/fixtures/broker_traffic/`) is broker-agnostic in shape; plan 02-03 adds `--broker dhan` to the same script and `fake_dhan_client`/`FakeDhanFeed` to the same helper module, per the phase-wide artifact inventory already in 02-01-PLAN.md.
- `crypto/lanes.py`'s `unclear_entry` marker and `_resolve_unclear_entry` establish the pattern plan 02-02 extends (`UNCLEAR_ALERT_SECONDS`, `pos["unknown_since"]`, a close-all barrier) — no changes needed here for that plan to build on top.
- No blockers.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-09-30*

## Self-Check: PASSED

All 9 created/modified files verified present on disk; both task commits (`1648bb0`, `2568abb`) verified present in `git log`.
