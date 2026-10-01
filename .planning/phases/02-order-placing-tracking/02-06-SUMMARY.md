---
phase: 02-order-placing-tracking
plan: 06
subsystem: infra
tags: [websocket, dhan, tick-feed, fault-injection, asyncio]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking (plan 02-05)
    provides: India broker-disconnect hardening patterns (strict reads, reconcile-on-drift) this plan continues for the tick feed
  - phase: 02-order-placing-tracking (plan 02-01)
    provides: scripts/capture_broker_traffic.py and tests/_fake_brokers.py scaffolding (REDACT_KEYS, redact, post-write secret scan, FIXTURES/load_traffic)
provides:
  - "run_feed() flushes buffered ticks in a finally around the receive loop — a hard drop or a shutdown during an outage no longer strands received ticks in memory"
  - "A server disconnect packet leaves the receive loop immediately instead of waiting on a dead stream until the 90s stall timer"
  - "FeedState.on_tick_errors counts stop-trigger handler failures instead of swallowing them silently; exposed via GET /api/tick-feed"
  - "Real recorded Dhan tick-feed frames (tests/fixtures/broker_traffic/dhan_feed.jsonl) and a FakeDhanFeed fault-injection harness for replaying drop/reconnect/shutdown scenarios against the real run_feed()"
affects: [02-07, dashboard tick-feed status display]

# Actuals (#2632)
actuals:
  tokens: 21612
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Buffer declared outside the reconnect loop, flushed in a finally around the inner receive loop — any exit path (drop, shutdown, cancellation) writes what was received instead of relying on a later connection's first periodic flush"
    - "FakeDhanFeed: per-connection session lists of bytes/exception/callable/WAIT-sentinel items, replayed through the real monkeypatched websockets.connect — tests exercise the real run_feed() against real recorded frames, not a mocked decode path"

key-files:
  created:
    - tests/fixtures/broker_traffic/dhan_feed.jsonl
  modified:
    - index_ai/tick_feed.py
    - scripts/capture_broker_traffic.py
    - tests/_fake_brokers.py
    - tests/test_tick_feed.py

key-decisions:
  - "last_error is no longer reset to None on every successful connect — a disconnect-triggered reconnect was clobbering the 'server sent disconnect' message before anyone could read it back. It is genuinely the *last* error, not a live 'current error' flag."
  - "Task 1 and Task 2's changes to tick_feed.py's run_feed are tightly coupled (Task 2's 'leave the receive loop, not just the for-loop' literally depends on Task 1's try/finally restructuring) — committed as two separate, individually-passing commits by temporarily reverting Task 2's pieces, verifying Task 1 alone, then re-applying and verifying the full state, rather than one combined commit."
  - "trading-safety-reviewer subagent could not be spawned directly in this execution context (no Task/Agent tool available to this executor) — its checklist was applied manually instead (see Deviations) and documented as such rather than skipped silently."

patterns-established:
  - "FeedState fields are diagnostic history (\"last X\"), not live status flags — as_dict()'s connected/stalled booleans are the live status; last_error persists until overwritten by the next real event."

requirements-completed: [ORD-03]

coverage:
  - id: D1
    description: "A hard Dhan feed drop (ConnectionClosedError) or a shutdown while the feed is down no longer loses ticks already received — everything buffered is flushed before the next connection opens or before the process exits"
    requirement: ORD-03
    verification:
      - kind: unit
        ref: "tests/test_tick_feed.py#test_hard_drop_flushes_received_ticks_before_reconnecting"
        status: pass
      - kind: unit
        ref: "tests/test_tick_feed.py#test_shutdown_during_outage_does_not_lose_received_ticks"
        status: pass
      - kind: unit
        ref: "tests/test_tick_feed.py#test_normal_stop_writes_tail_once_with_no_drop"
        status: pass
    human_judgment: false
  - id: D2
    description: "A server 'disconnect' packet makes the feed reconnect at once instead of waiting out the 90s stall timer on a dead stream"
    requirement: ORD-03
    verification:
      - kind: unit
        ref: "tests/test_tick_feed.py#test_server_disconnect_reconnects_without_waiting_for_stall"
        status: pass
    human_judgment: false
  - id: D3
    description: "A failure inside the stop-trigger handler (on_tick) is counted and surfaced in last_error instead of being swallowed, and the tick is still written to the log regardless"
    requirement: ORD-03
    verification:
      - kind: unit
        ref: "tests/test_tick_feed.py#test_on_tick_failure_is_counted_and_surfaced_but_tick_still_written"
        status: pass
    human_judgment: false
  - id: D4
    description: "Under a simulated busy scan cycle (25 concurrent candle-scoring tasks) with a mid-stream drop and reconnect, every tick reaches on_tick and the tick log exactly once and the feed resubscribes on the new connection"
    requirement: ORD-03
    verification:
      - kind: unit
        ref: "tests/test_tick_feed.py#test_busy_scan_cycle_no_tick_loss_and_resubscribes_on_reconnect"
        status: pass
    human_judgment: false
  - id: D5
    description: "Tests replay real recorded Dhan feed frames (D-03) rather than synthetic ones"
    requirement: ORD-03
    verification:
      - kind: unit
        ref: "tests/test_broker_traffic_fixtures.py"
        status: pass
    human_judgment: false
  - id: D6
    description: "The money-path change (tick-driven stop trigger) was reviewed against trading-safety-reviewer's checklist"
    human_judgment: true
    rationale: "The review was performed manually by this executor rather than by spawning the trading-safety-reviewer subagent (no Task/Agent tool available in this execution context) — a human should confirm the manual review (documented under Deviations) was adequate, or re-run the actual subagent."

duration: ~35min (this session; resumed twice before, see Deviations)
completed: 2026-10-01
status: complete
---

# Phase 2 Plan 06: Dhan tick-feed reconnect hardening Summary

**The Dhan tick feed now flushes every received tick before a reconnect or shutdown, reconnects immediately on a server disconnect instead of waiting 90 seconds, and no longer hides a broken stop-trigger handler — all proven by replaying real recorded Dhan feed frames.**

## Performance

- **Duration:** ~35 min this session (third and final attempt — see Deviations for the two prior interrupted sessions)
- **Completed:** 2026-10-01
- **Tasks:** 2
- **Files modified:** 5 (1 created, 4 modified)

## Accomplishments

- `run_feed()`'s receive loop is now wrapped in `try`/`finally`; the buffer (which lives across reconnects) is flushed on every exit path — a hard `ConnectionClosedError` or a shutdown while the feed is down no longer strands received ticks in memory.
- A code-50 "disconnect" packet now makes the feed leave the receive loop itself (not just the inner for-loop), so the existing backoff reconnects right away instead of sitting on a dead stream until `STALL_SECONDS` (90s) elapses.
- `FeedState.on_tick_errors` counts failures from the stop-trigger handler (`index_ai/scanner.py::on_index_tick`) instead of swallowing them with a bare `except Exception: pass`; `last_error` names the exception. Exposed via `GET /api/tick-feed`. The tick is still buffered and written regardless of whether `on_tick` succeeds, since buffering happens before the handler runs.
- Fixed a bug this surfaced: `last_error` was being reset to `None` the instant any new connection opened — including the reconnect that followed a disconnect — which wiped the "server sent disconnect" message before it could ever be observed. `last_error` is no longer reset on connect.
- `scripts/capture_broker_traffic.py` gained `--broker feed --feed-seconds N`: a read-only capture of real Dhan tick-feed frames (never prints/stores the URL or token), written to `tests/fixtures/broker_traffic/dhan_feed.jsonl` as base64 with a post-write secret scan, run during NSE market hours on 2026-10-01.
- `tests/_fake_brokers.py` gained `load_feed_frames()` and `FakeDhanFeed` — a `websockets.connect` replacement that replays per-connection session lists of real frames / exceptions / callables / a `WAIT` sentinel, letting tests drive the real `run_feed()` through drop, shutdown, disconnect, handler-failure, and busy-cycle scenarios.
- 6 new tests in `tests/test_tick_feed.py`, all replaying the real captured frames.

## Task Commits

1. **Task 1: Record real Dhan feed frames, replay them with a hard drop, and flush every received tick before reconnecting** - `8d77da8` (fix) — capture script `--broker feed`, `dhan_feed.jsonl` fixture, `FakeDhanFeed` harness, the try/finally flush fix in `run_feed`, and 3 tests (hard-drop, shutdown-during-outage, normal-stop).
2. **Task 2: Reconnect at once on a server disconnect, surface stop-handler failures, and prove it all under a busy scan cycle** - `09af74d` (fix) — disconnect-leaves-the-receive-loop behavior, `FeedState.on_tick_errors`, the `last_error`-not-reset-on-connect fix it required, and 3 tests (server-disconnect, on-tick-failure, busy-scan-cycle).

**Plan metadata:** committed alongside this SUMMARY (see commit history).

_Note: both tasks are `fix` commits (bug fixes to existing `run_feed` behavior), not `feat` — ORD-03 is a correction to already-shipped tick-feed code, not new functionality._

## Files Created/Modified

- `index_ai/tick_feed.py` - `run_feed`'s receive loop wrapped in try/finally for guaranteed flush; disconnect packet leaves the loop immediately; `FeedState.on_tick_errors` added and exposed in `as_dict()`; `last_error` no longer reset on connect
- `scripts/capture_broker_traffic.py` - `--broker feed --feed-seconds N` for read-only real tick-feed capture
- `tests/fixtures/broker_traffic/dhan_feed.jsonl` - 274 real captured frames (296 quote packets, security ids 13/25/51 = NIFTY/BANKNIFTY/SENSEX), captured 2026-10-01T03:51 UTC (09:21 IST, NSE market hours, Thursday, not a holiday)
- `tests/_fake_brokers.py` - `load_feed_frames()`, `FakeDhanFeed` fault-injection harness
- `tests/test_tick_feed.py` - 6 new fault-injection tests replaying real frames through the real `run_feed()`

## Decisions Made

- **last_error persistence:** removed the unconditional `_state.last_error = None` on every successful connect. It was clobbering the disconnect message the moment the reconnect succeeded, before `GET /api/tick-feed` or a test could ever read it. `last_error` is now genuinely the *last* error recorded, not a "currently erroring" flag — this matches the field's own name and is a strict improvement for the dashboard (a disconnect event is now visible after the fact instead of flashing and disappearing).
- **Commit split:** Task 1 and Task 2's code changes to `run_feed` are tightly coupled (Task 2's "leave the receive loop, not just the for-loop" depends directly on Task 1's try/finally restructuring, and both land in the same function). To honor "atomic per-task commits" without just bundling everything into one commit, Task 2's pieces (`on_tick_errors`, the disconnect-leaves-loop flag, the `last_error`-not-reset fix, and its 3 tests) were temporarily reverted, Task 1 was verified standalone (ran its own 3 tests + the shared fixture tests green), committed, then Task 2's pieces were re-applied, verified against its own + the full `tick_feed`/`tick_driven_stops`/`fast_trail_loop` suite, and committed separately.
- **Trading-safety-reviewer:** the plan's Task 2 action explicitly calls for running the `trading-safety-reviewer` subagent. This execution context had no Task/Agent tool available to spawn a named subagent (only Read/Write/Edit/Bash/Grep/Glob/Skill/SubagentHandback). Rather than skip the review, it was performed manually against the agent's own published checklist (`.claude/agents/trading-safety-reviewer.md`) — see Deviations below for the findings.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `last_error` reset on connect clobbered the disconnect message**
- **Found during:** Task 2, while fixing the one failing test left by the interrupted prior session (`test_server_disconnect_reconnects_without_waiting_for_stall`)
- **Issue:** `_state.last_error = None` ran unconditionally at the top of every successful connection, including the reconnect triggered by a disconnect packet. By the time a test (or the dashboard) could observe `last_error` after the reconnect, it had already been wiped back to `None` — confirmed by instrumenting the failure: `FeedState(connected=True, reconnects=1, last_error=None)` immediately after the second connection opened.
- **Fix:** Removed the `_state.last_error = None` line; added a comment explaining why (it is the *last* error, not a live status flag).
- **Files modified:** `index_ai/tick_feed.py`
- **Verification:** `test_server_disconnect_reconnects_without_waiting_for_stall` passes; full `tests/test_tick_feed.py` (14 tests) green; no other test depended on the reset behavior.
- **Committed in:** `09af74d` (Task 2 commit)

**2. [Judgment call - process] trading-safety-reviewer run manually, not via subagent spawn**
- **Found during:** Task 2's final action item ("run the trading-safety-reviewer subagent on this plan's diff")
- **Issue:** This executor's tool set had no Task/Agent-spawning capability to invoke the named subagent.
- **Fix:** Applied the subagent's own published checklist (`.claude/agents/trading-safety-reviewer.md`) by hand against the diff:
  - *Blocking I/O on the event loop:* No new blocking calls added. `on_tick` is called synchronously within `run_feed`'s event loop, which is pre-existing, unchanged behavior; `index_ai/scanner.py::on_index_tick` (the actual handler wired in via `server.py`) only does dict bookkeeping and schedules an `asyncio` task — no sync I/O. Clean.
  - *Read-modify-write races:* N/A — no shared file or settings state touched, only the in-process `FeedState` dataclass written from a single task.
  - *Live-arming interlock / cost-model shortcuts / order sequencing:* N/A — `executor.py`, `dhan_orders.py`, `config.py` trading flags, `charges.py` untouched by this plan.
  - *Money-path specific:* confirmed `buffer.append(pkt)` happens *before* `on_tick(pkt)` is called, so a failing stop-trigger handler can no longer cause a tick to be dropped from the log — it only loses visibility into the stop-trigger call itself, which is now counted and surfaced (`on_tick_errors`, `last_error`) instead of silently swallowed. This directly closes threat `T-02-29` (Repudiation, medium severity) from the plan's own threat register.
  - *Prohibitions respected:* `STALL_SECONDS`, `FLUSH_SECONDS`, `MAX_BACKOFF` and the subscribe message are byte-identical to before this plan (grep-verified, see Acceptance Criteria below) — the disconnect fix only changes which loop is exited, not the backoff/timer values.
  - No findings requiring further action.
- **Files modified:** none (review only)
- **Verification:** manual checklist walk-through documented above; a human should confirm this was adequate or re-run the actual `trading-safety-reviewer` subagent.
- **Committed in:** N/A (process note, not a code change)

---

**Total deviations:** 2 (1 auto-fixed bug, 1 process substitution)
**Impact on plan:** The `last_error` fix was necessary for Task 2's own acceptance criteria to hold (last_error must say "disconnect" after the reconnect) — no scope creep, it's the actual bug the failing test was pointing at. The manual safety review is a process deviation, not a code deviation; flagged above for human confirmation rather than silently presented as the real subagent's output.

## Issues Encountered

- **Two prior interrupted sessions** (per the `<resume_context>` given to this run): the first was correctly blocked at a legitimate precondition (market closed, live Dhan feed capture returned zero frames — left uncommitted per the "no partial commit" rule). The second resumed after market open, successfully captured real frames and wrote most of the Task 1 + Task 2 code, but was interrupted by a tool/session error before anything was committed or a SUMMARY written. Neither was a plan or code problem. This session picked up the uncommitted working tree, verified it line-by-line against the plan's actual action/acceptance-criteria text (not just trusted the resume summary), found and fixed the one real bug (`last_error` reset-on-connect clobbering the disconnect message — the resume context's guess of "set last_error on the disconnect path" was already correctly implemented; the actual defect was the *unrelated* reset immediately undoing it), split the already-combined diff into two atomic task commits, and ran the full repo suite twice (mid-split and at the end) to confirm no regressions.
- `tests/fixtures/broker_traffic/dhan_feed.jsonl` was captured at `2026-10-01T03:51:05 UTC` = 09:21 IST — inside NSE market hours (09:15–15:30 IST) on a Thursday that `index_ai/market_holidays.is_nse_holiday("2026-10-01")` confirms is not a holiday. 274 frames, 296 decoded packets, all `type: "quote"`, across security ids 13 (NIFTY), 25 (BANKNIFTY), 51 (SENSEX, per the subscription list) — real market data, no synthetic frames needed.

## User Setup Required

None - no external service configuration required. `ENABLE_TICK_FEED` remains off by default as before; no new env keys were added.

## Next Phase Readiness

- ORD-03 is closed: the Dhan tick feed survives a reconnect mid-scan-cycle without silently dropping tick data a stop trigger depends on, verified under a simulated busy cycle of 25 concurrent candle-scoring tasks (phase success criterion 3).
- Plan 02-07 (the next plan in this phase) can proceed — its own stated purpose (the 20-second price-check fallback covering any gap) is independent of this plan's fixes and was not blocked by them.
- `GET /api/tick-feed` now additionally reports `on_tick_errors`; if the dashboard's tick-feed panel surfaces `last_error`/counters, it may be worth a quick visual check that the new field renders sanely (not required by this plan's own scope, flagging for awareness only).
- **Recommend a human (or a future session with Task/Agent access) re-run the actual `trading-safety-reviewer` subagent** against commit range `cfc59af..09af74d` to confirm the manual review above didn't miss anything a dedicated pass would catch — this plan's own money-path caution (CLAUDE.md) is why that subagent exists.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-10-01*

## Self-Check: PASSED

All claimed files found on disk (`index_ai/tick_feed.py`, `scripts/capture_broker_traffic.py`,
`tests/fixtures/broker_traffic/dhan_feed.jsonl`, `tests/_fake_brokers.py`, `tests/test_tick_feed.py`,
this SUMMARY). Both task commits (`8d77da8`, `09af74d`) found in `git log`. Full repo suite:
760 passed (up from the pre-plan baseline of 754, exactly the 6 new tests added here).
