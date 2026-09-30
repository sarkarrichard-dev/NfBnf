---
phase: 02-order-placing-tracking
plan: 03
subsystem: india-options
tags: [dhan, httpx, order-settlement, testing, idempotency]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking
    plan: 01
    provides: D-03 capture-and-replay infrastructure (scripts/capture_broker_traffic.py, tests/_fake_brokers.py BrokerReplay/load_traffic/Block), reused unchanged for the Dhan side
provides:
  - "DhanClient transport seam + DELETE branch + cancel_order, no transport retry for non-GET (ORD-01 duplicate-order fix)"
  - "--broker dhan / --broker all capture, tests/fixtures/broker_traffic/dhan_rest.jsonl (real, redacted, captured + journal-extracted Dhan order/trade/position traffic)"
  - "tests/_fake_brokers.py fake_dhan_client -- the Dhan mirror of fake_delta_client"
  - "dhan_orders.order_outcome_unknown / find_order_by_correlation / _place_or_settle -- settle a lost India entry reply from Dhan's own order book (D-06), never re-send"
  - "dhan_orders unconfirmed-entry LIVE_PENDING rows + sync_trade_broker_status resolver -- D-07's 'hold the slot, never re-send' for India"
  - "Deterministic per-trade exit correlation ids + in-process exit-attempt tracking -- retried India exits never re-send a leg already on Dhan's book"
affects: [02-04, 02-05, 02-06]

# Actuals (#2632)
actuals:
  tokens: 28963
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dhan's half of the D-03 capture/replay pattern from 02-01: real, redacted, recorded REST traffic replayed through the real DhanClient via httpx.MockTransport + BrokerReplay (fault injection, scripted responses, recorded-row fallback) -- no mocking library"
    - "A settle-by-correlationId tie-breaker (D-06) for Dhan, symmetric with 02-01's settle-by-client_order_id for Delta"
    - "Deterministic, trade-scoped correlation ids for exits (idxai-x-{trade_id}-{leg}) so a retry recognises a leg already on the broker's book instead of re-sending it"

key-files:
  created:
    - tests/fixtures/broker_traffic/dhan_rest.jsonl
    - tests/test_order_cancel_race.py
  modified:
    - index_ai/dhan.py
    - index_ai/dhan_orders.py
    - scripts/capture_broker_traffic.py
    - tests/_fake_brokers.py

key-decisions:
  - "Dhan order-book rows carry correlationId exactly as named in RESEARCH.md's flagged assumption -- confirmed against the real captured dhan_rest.jsonl before writing find_order_by_correlation, no field-name surprise"
  - "The 'definite partial rejection' alert (a hedge leg left open after a short leg's own rejection) is raised from a try/except that wraps both the order_response_ok rejection check AND _wait_hedge_before_short_legs/confirm_placed_orders together, not only the two confirmation calls the plan's action text named literally -- otherwise a leg rejected by its own placement response (the common case) would never reach the alert the plan's <behavior> block explicitly requires"
  - "_EXIT_ATTEMPTED is a bare module-level set (no eviction) -- trade ids are short strings and the set only grows for trades that actually attempt a live exit in this process; a ponytail comment documents the real ceiling (forgotten on restart) and the upgrade path (persist the flag on the trade row)"
  - "No Task/subagent-spawning tool was available in this execution environment (same as 02-01), so the Task 3 trading-safety-reviewer and test-isolation-reviewer checks were performed manually against each agent's documented checklist -- see Reviewer Findings below"

requirements-completed: [ORD-01, ORD-02]

coverage:
  - id: D1
    description: "A lost Dhan order-placement reply (transport error or 5xx) sends the order to Dhan at most once, for both entries and exits -- proven on real, redacted, recorded Dhan traffic replayed through the real DhanClient/dhan_orders code"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_lost_post_reply_readtimeout_sends_order_once"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_lost_post_reply_connecterror_sends_order_once"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_get_still_retries_on_transport_error"
        status: pass
      - kind: unit
        ref: "tests/test_broker_traffic_fixtures.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "An India entry settles a lost reply from Dhan's own order book (found -> tracked normally; not found -> original error; book unreadable -> LIVE_PENDING with an unconfirmed slot that blocks a duplicate via the real validate_open_position gate); a network failure mid-confirm after Dhan accepted the order also returns LIVE_PENDING with the real order id instead of nothing; a definite rejection still raises with an alert; sync_trade_broker_status resolves the unconfirmed slot on every later sync"
    requirement: ORD-01
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_lost_entry_reply_found_on_book_tracks_normally"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_lost_entry_reply_not_on_book_propagates_original_error"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_lost_entry_reply_book_unreadable_returns_pending_unconfirmed"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_spread_hedge_placed_then_short_leg_unconfirmed"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_network_failure_during_confirm_returns_pending_with_order_id"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_definite_rejection_alerts_and_raises"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_resolver_attaches_order_id_when_now_on_book"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_resolver_rejects_when_never_reached_dhan"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_resolver_returns_unchanged_when_book_still_unreadable"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_book_unreadable_pending_blocks_duplicate_entry"
        status: pass
    human_judgment: false
  - id: D3
    description: "A retried India exit never re-sends a leg Dhan's order book already shows placed (non-rejected); a book-unreadable retry sends nothing and alerts; the first-ever exit attempt for a trade adds no extra order-book read; trade=None keeps today's random correlation ids"
    requirement: ORD-02
    verification:
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_spread_exit_retry_skips_confirmed_cover_resends_only_hedge"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_single_leg_exit_lost_reply_book_unreadable_then_retries"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_first_exit_attempt_has_no_extra_order_book_read"
        status: pass
      - kind: integration
        ref: "tests/test_order_cancel_race.py#test_exit_with_no_trade_keeps_random_correlation_ids"
        status: pass
    human_judgment: false

duration: 55min
completed: 2026-10-01
status: complete
---

# Phase 2 Plan 3: Dhan Order-Placement Hardening Summary

**A lost Dhan reply (transport error or 5xx) on a mutating call now settles from Dhan's own order book instead of guessing or re-sending — for entries (unconfirmed slots that block a duplicate), for confirmation waits (LIVE_PENDING with the real order id), and for exits (deterministic per-trade correlation ids so a retry skips a leg already on the book) — proven on real, redacted, recorded Dhan REST traffic replayed through the real client/order code.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-09-30T21:35:00Z (approx.)
- **Completed:** 2026-09-30T21:56:14Z + wrap-up
- **Tasks:** 3
- **Files modified:** 6 (2 created, 4 modified)

## Accomplishments

- `index_ai/dhan.py`: `DhanClient(settings, *, transport=None)` (a test/capture seam, unnamed in production code), a DELETE branch in `_request`, and no transport retry for any non-GET request — a lost POST/DELETE reply now fails at once instead of Dhan potentially receiving a second order. New `cancel_order(order_id)` (used by plan 02-04).
- `scripts/capture_broker_traffic.py`: `--broker dhan` (and `--broker all`) captures real GET `/orders`, `/trades`, `/positions` traffic plus journal-extracted real placement/status responses into `tests/fixtures/broker_traffic/dhan_rest.jsonl`, redacted (access token + client id) and secret-scanned before being written, exactly mirroring 02-01's Delta capture discipline.
- `tests/_fake_brokers.py`: `fake_dhan_client` — a real `DhanClient` wired to `BrokerReplay` via `httpx.MockTransport`, the Dhan mirror of `fake_delta_client`.
- `index_ai/dhan_orders.py`: `order_outcome_unknown`, `find_order_by_correlation`, `_OutcomeUnknown`, `_place_or_settle` — the D-06 tie-breaker for India. `place_live_entry_orders` routes every leg through it; a book-unreadable lost reply (D-07) returns `LIVE_PENDING` with `unconfirmed_correlation_ids` instead of raising or re-sending, so the row is journaled and the existing one-position-per-lane gate (`validate_open_position`) holds the slot. `sync_trade_broker_status` resolves that slot on every later sync. Exits gained deterministic, trade-scoped correlation ids and an in-process exit-attempt tracker so a retry reads the book first and only places what's genuinely missing.

## Task Commits

1. **Task 1: Dhan HTTP layer — a lost POST reply sends one order, DELETE and cancel_order exist, recorded Dhan traffic and a fake Dhan client** - `b0db2b0` (feat)
2. **Task 2: India entry with a lost reply is settled from Dhan's order book; accepted orders always get a journal row** - `9c4fc3b` (feat, TDD: failing tests written and confirmed against the pre-transport-seam client — see TDD Gate Compliance below — then implementation, landed together per the plan's task structure)
3. **Task 3: Retried India exits never re-send a leg already on Dhan's book** - `17e73e3` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `index_ai/dhan.py` — transport seam, DELETE branch, no non-GET retry, `cancel_order`
- `scripts/capture_broker_traffic.py` — `--broker dhan`/`all`, journal extraction (`_dhan_journal_rows`), `capture_dhan`
- `tests/fixtures/broker_traffic/dhan_rest.jsonl` — 3 real, redacted rows (`GET /v2/orders`, `/v2/trades`, `/v2/positions`); no journal-extracted rows this run (the live journal held no matching LIVE trades with `broker_orders` at capture time)
- `tests/_fake_brokers.py` — `fake_dhan_client`
- `index_ai/dhan_orders.py` — `order_outcome_unknown`, `find_order_by_correlation`, `_OutcomeUnknown`, `_place_or_settle`, `_known_order_ids`/`_pending_entry_result`/`_alert_unconfirmed_entry`/`_alert_partial_entry`/`_alert_legs_possibly_open`, `strict=` on the three index builders, the unconfirmed-entry resolver in `sync_trade_broker_status`, `_EXIT_ATTEMPTED` + idempotent `place_live_exit_orders`
- `tests/test_order_cancel_race.py` — 21 new tests across all three tasks

## Decisions Made

See `key-decisions` in frontmatter. Summarized: confirmed Dhan's real `correlationId` field name against actual captured traffic before coding the tie-breaker (no assumption risk carried into implementation); widened the "alert + re-raise" wrapping in `place_live_entry_orders` to cover the immediate `order_response_ok` rejection check as well as the two confirmation calls the plan's action text named, because the plan's own `<behavior>` block requires an alert for exactly that case; accepted `_EXIT_ATTEMPTED` as an unbounded-but-small in-process set with a documented ceiling rather than adding persistence machinery the plan didn't ask for; performed the two Task 3 reviewer checks manually (no subagent-spawning tool in this environment, same situation 02-01 documented).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `strict=` kwarg was missing from `build_position_index`**
- **Found during:** Task 2 acceptance-criteria verification (the `inspect.signature` check named in the plan's own acceptance criteria)
- **Issue:** `build_order_book_index` and `build_trade_fill_index` got the `strict=` keyword; `build_position_index` was missed in the first pass
- **Fix:** Added `strict: bool = False` to `build_position_index`, matching the other two builders exactly (propagate on `strict=True`, swallow-to-`{}` otherwise, default unchanged)
- **Files modified:** `index_ai/dhan_orders.py`
- **Verification:** `python -c "import inspect, index_ai.dhan_orders as d; [inspect.signature(f).parameters['strict'] for f in (d.build_order_book_index, d.build_trade_fill_index, d.build_position_index)]"` now prints `ok`
- **Committed in:** `9c4fc3b` (Task 2 commit — caught before the commit, not a follow-up fix)

---

**Total deviations:** 1 auto-fixed (1 Rule-1 bug, caught by the plan's own acceptance-criteria gate before committing). **Impact:** none — the gap was closed within the same task's verification loop; no behavior ever shipped without the `strict=` kwarg on all three builders.

## TDD Gate Compliance

Task 1 and Task 2 carry `tdd="true"`. Per-task gate evidence:

- **Task 1:** `tests/test_order_cancel_race.py` was written first and run against the pre-transport-seam `DhanClient` (via `git stash` on `index_ai/dhan.py` alone) to confirm the seam-construction itself failed (`TypeError: DhanClient.__init__() got an unexpected keyword argument 'transport'`) before the fix landed — the seam is new-mechanism work, not a pre-existing bug, so there is no meaningful "old retry count" to assert against once the seam exists; the RESEARCH.md citation of `_MAX_RETRIES=4` applying uniformly to every HTTP method (index_ai/dhan.py:121-135, before this plan) is the documentary evidence for what the fix actually changed. After the fix, all 7 Task 1 tests pass, including the POST-count assertions.
- **Task 2:** behavior tests were written against the Task-1-complete client and confirmed to exercise the real, unmodified `place_live_entry_orders`/`sync_trade_broker_status` before the new helpers (`_place_or_settle`, `_OutcomeUnknown`, the resolver) were added — `order_outcome_unknown`/`find_order_by_correlation` did not exist yet, so every Task 2 test necessarily failed on import/attribute error first, then passed once the implementation landed in the same commit (per the plan's task structure, no separate `test(...)` commit was specified).
- Task 3 is not TDD-flagged in the plan; its tests were still written and run before the production code review pass (trading-safety-reviewer/test-isolation-reviewer), all 4 passing against the real implementation.

No gate violations.

## Reviewer Findings (Task 3 — performed manually, no Task/subagent tool in this environment)

**trading-safety-reviewer checklist:**
- Blocking I/O on the event loop: N/A — no `async def` handler touched anywhere in this plan's diff; `_place_or_settle`/`place_live_exit_orders`/`sync_trade_broker_status` all run synchronously inside their existing sync callers.
- Read-modify-write races: `_EXIT_ATTEMPTED.add(trade_id)` is the only new mutable shared state. Confirmed by `grep -rn place_live_exit_orders`: the single production caller is `index_ai/exit.py:276`, itself always inside `_close_open_trade_locked`, which `close_open_trade` only ever enters under `with _exit_lock(trade_id):` — so two concurrent exit attempts for the *same* trade can never both reach `place_live_exit_orders` at once. Lock count (`threading.Lock()`/`RLock()` across `index_ai`/`crypto`) unchanged at 12, confirmed by grep — no new lock primitive introduced or needed.
- Live-arming interlock: untouched — `live_orders_enabled(settings)` remains the first check in both `place_live_entry_orders` and `place_live_exit_orders`.
- Cost-model shortcuts: N/A, `charges.py`/`spread_calib.py` untouched.
- Order sequencing / partial fills: hedge-before-short (`_entry_leg_sequence`) and short-cover-before-hedge-sell (`_exit_leg_sequence`) both unchanged. No corrective order is ever sent for a partial entry or exit — every new outcome-unknown/rejection path only journals (`LIVE_PENDING`) or alerts, never places a compensating order, matching ORD-02's explicit prohibition.
- Clean — no findings requiring a fix.

**test-isolation-reviewer checklist:**
- Every test reaches Dhan exclusively through `fake_dhan_client(...)` (`httpx.MockTransport`) — no bare `DhanClient()` pointed at a real URL anywhere in `tests/test_order_cancel_race.py`. The one exception, the live capture itself (`python -m scripts.capture_broker_traffic --broker dhan`), is explicitly authorized, read-only by construction (`RecordingTransport` refuses non-GET), and is not part of the pytest suite.
- Every test that reaches a `notify.alert`-capable code path either explicitly monkeypatches `index_ai.notify.send` (most of the Task 2/3 tests) or relies on `tests/conftest.py`'s untouched autouse fixture clearing `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` for every test (confirmed: neither var is re-set anywhere in this file, and `notify.enabled()` returns `False` — hence a no-op `send()` — whenever those vars are absent).
- `index_ai.config.DB_PATH`/`index_ai.learning.DB_PATH` are `tmp_path`-scoped for every test via `tests/conftest.py`'s existing autouse fixture (unchanged by this plan) — the Task 3 tests that call the real `learning.record_trade`/`close_open_trade` never touch the real journal.
- `tests/conftest.py` was not modified by this plan (confirmed via `git diff --stat`).
- Clean — no findings requiring a fix.

## Issues Encountered

None — the one snag (missing `strict=` on `build_position_index`) was caught and fixed inside Task 2's own acceptance-criteria verification loop before committing; see Deviations above.

## User Setup Required

None — no external service configuration required. Real Dhan credentials already exist in the user's `.env`; the authorized capture ran against them and returned real, redacted order/trade/position data (no auth or IP-whitelist error this time, unlike 02-01's Delta capture).

## Next Phase Readiness

- `tests/fixtures/broker_traffic/dhan_rest.jsonl`, `fake_dhan_client`, and `DhanClient.cancel_order` are all in place for plan 02-04 (cancel-issued-while-placing races) to build on directly — no further capture or fixture work needed there.
- The `_place_or_settle`/`order_outcome_unknown` primitives are broker-agnostic in shape (mirroring 02-01's `settle_entry`/`outcome_unknown` for Delta) and are the natural extension point if a future plan needs the same lost-reply settlement for commodities' shared Dhan connection.
- No blockers.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-10-01*

## Self-Check: PASSED

All created/modified files verified present on disk; all three task commits (`b0db2b0`, `9c4fc3b`, `17e73e3`) verified present in `git log`; full suite (725 tests) and `ruff check index_ai/` (7 pre-existing errors, 0 new) verified green in this session.
