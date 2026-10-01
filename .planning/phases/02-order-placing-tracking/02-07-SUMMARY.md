---
phase: 02-order-placing-tracking
plan: 07
subsystem: trading-safety
tags: [tick-feed, fallback, dashboard, react-query, documentation]

# Dependency graph
requires:
  - phase: 02-order-placing-tracking (plan 02-06)
    provides: index_ai/tick_feed.py's FeedState (connected/stalled/last_tick_at/on_tick_errors), surfaced via GET /api/tick-feed, which this plan's test and pill both read
provides:
  - "A test proving a crossed stop still closes on the 20-second REST price check when the tick feed is forced down (ENABLE_TICK_FEED=true, connected=False, stalled last_tick_at) and no tick ever crossed the stop"
  - "A test proving the fallback does not close a trade early when the fetched price has not crossed the stop"
  - "A test proving _fast_trail_loop keeps calling _check_trails every TRAIL_FAST_SECONDS regardless of whether the tick feed is live, stalled, or off"
  - "guides/Strategy Guide.md section explaining the three stop-checking layers, the fallback's worst-case delay, feed reconnect behaviour, and the dashboard pill's four states"
  - "Corrected Execution safety bullets: a still-pending Live entry is cancelled before a close is treated as an exit; a lost order reply is looked up by our own order tag, never re-sent; reconciliation problems go to Telegram"
  - "A Ticks status pill in the dashboard top bar reading the existing GET /api/tick-feed (Ticks live / Ticks: fallback / Ticks idle / Ticks off), each with a one-line tooltip"
affects: [phase-3-exit-optimisation, phase-4-data-health-view]

# Actuals (#2632)
actuals:
  tokens: 3297
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dashboard status pills read a lightweight existing endpoint via useQuery(['tick-feed']) mirroring TickerStrip's useQuery+api<T>() pattern, never a new backend route"

key-files:
  created: []
  modified:
    - tests/test_fast_trail_loop.py
    - guides/Strategy Guide.md
    - dashboard/src/components/shell/StatusPills.tsx

key-decisions:
  - "ui-consistency-reviewer's 7-point checklist applied manually against the StatusPills.tsx diff (no Task/Agent tool available in this execution context, same situation plan 02-06 documented for trading-safety-reviewer) rather than skipped -- documented under Deviations with the per-point findings (none)."
  - "The Strategy Guide's 'Trailing stop (two phases)' point-value table was left untouched per the plan's own instruction (Phase 3 owns those numbers), even though it is already known to read stale values (100/200-pt initial stops) versus the current 25/55/80-pt scalp trail in instruments.py -- out of scope for this plan, not re-verified here."

requirements-completed: [ORD-04]

coverage:
  - id: D1
    description: "A crossed stop still closes on the 20-second price check when the tick feed is switched on but down, and the fallback does not close early when the stop has not been crossed"
    requirement: ORD-04
    verification:
      - kind: unit
        ref: "tests/test_fast_trail_loop.py#test_crossed_stop_closes_on_the_price_check_when_the_tick_feed_is_down"
        status: pass
      - kind: unit
        ref: "tests/test_fast_trail_loop.py#test_stop_not_crossed_the_fallback_does_not_close_early"
        status: pass
    human_judgment: false
  - id: D2
    description: "The 20-second check keeps calling _check_trails regardless of whether the tick feed is live, stalled, or off"
    requirement: ORD-04
    verification:
      - kind: unit
        ref: "tests/test_fast_trail_loop.py#test_fast_loop_runs_while_the_tick_feed_is_stalled_or_off"
        status: pass
    human_judgment: false
  - id: D3
    description: "guides/Strategy Guide.md documents the three stop-checking layers, fallback behaviour, feed reconnects, and the dashboard pill's four states; Execution safety bullets corrected for plans 02-01 through 02-05"
    requirement: ORD-04
    verification:
      - kind: other
        ref: "python -c assertion script checking required section/phrase presence (see Task 2 verify)"
        status: pass
    human_judgment: false
  - id: D4
    description: "Dashboard top bar shows a Ticks pill reading GET /api/tick-feed with four states and tooltips, built only from the existing Pill component and design tokens"
    human_judgment: true
    rationale: "npm run build (tsc type check) passed and the ui-consistency-reviewer checklist was applied manually with no findings, but actual visual rendering and tooltip behaviour in the browser was not checked in this session -- a human should load the dashboard once to confirm."

duration: ~25min
completed: 2026-10-01
status: complete
---

# Phase 2 Plan 07: Tick-feed fallback proof, documentation, and dashboard indicator Summary

**Proved with a forced-down tick feed that a crossed stop still closes on the existing 20-second Dhan price check, wrote that three-layer behaviour into the Strategy Guide, and added a four-state Ticks pill to the dashboard top bar reading the already-existing `GET /api/tick-feed` — no change to `index_ai/scanner.py` or any backend endpoint.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-10-01
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- `tests/test_fast_trail_loop.py` gained three new tests: a NIFTY `BUY_CALL` trade at 23400 (25-point scalp trail, stop 23375) closes via the 20-second REST price check when the tick feed is forced down (`connected=False`, `last_tick_at` 600s stale, `tick_feed.status()["stalled"]` confirmed `True`) and no tick ever crossed the stop; the same setup with a fetched price above the stop does **not** close early; and `_fast_trail_loop` keeps calling `_check_trails` every cycle across three parametrized feed states (live, stalled, `ENABLE_TICK_FEED` unset). All three pass on today's code — `index_ai/scanner.py` was not touched.
- `guides/Strategy Guide.md` gained a new "Stop checks: live ticks and the 20-second fallback" section (three layers: per-tick, 20-second REST re-price, 90-second Supertrend refresh; what happens when ticks lag; the feed's own reconnect behaviour; the dashboard pill's four states; where to look — `GET /api/tick-feed`). The Execution safety section's Live bullet was corrected (a still-pending entry is cancelled before a close counts as an exit, not assumed closed on `LIVE_TRADED` alone) and two bullets were added for the lost-reply lookup and Telegram-on-reconciliation-problem behaviour already shipped in plans 02-01 through 02-05. The Trailing stop point-value table was left untouched, as instructed.
- `dashboard/src/components/shell/StatusPills.tsx` gained a `useQuery(['tick-feed'])` (20s `refetchInterval`, 10s `staleTime`, same shape as `TickerStrip`) and one more `Pill` after the Dhan pill: **Ticks live** (green, feed flowing), **Ticks: fallback** (amber, feed down while market open), **Ticks idle** (feed down while market closed), **Ticks off** (`ENABLE_TICK_FEED` off) — each with a tooltip stating how stops are being checked. Built only from the existing `Pill` component and CSS custom-property tokens; `App.tsx` and `index_ai/server.py` are unchanged.

## Task Commits

1. **Task 1: Prove a crossed stop still closes on the 20-second check when the tick feed is down** - `2060ad8` (test)
2. **Task 2: Write the tick-feed fallback into the Strategy Guide, and correct the execution-safety bullets** - `1eee06d` (docs)
3. **Task 3: Ticks status pill in the dashboard top bar** - `023d48c` (feat)

**Plan metadata:** committed alongside this SUMMARY (see commit history).

## Files Created/Modified

- `tests/test_fast_trail_loop.py` - three new tests proving the 20-second fallback closes a crossed stop with the tick feed forced down, does not close early when the stop hasn't been crossed, and that the fast loop runs regardless of feed state
- `guides/Strategy Guide.md` - new "Stop checks: live ticks and the 20-second fallback" section; corrected/added Execution safety bullets
- `dashboard/src/components/shell/StatusPills.tsx` - Ticks pill fed by the existing `GET /api/tick-feed`

## Decisions Made

- **ui-consistency-reviewer run manually, not via subagent spawn:** this execution context has no Task/Agent-spawning tool (only Read/Write/Edit/Bash/Grep/Glob/Skill/SubagentHandback), the same situation plan 02-06 hit for `trading-safety-reviewer`. Applied the agent's own published 7-point checklist (`.claude/agents/ui-consistency-reviewer.md`) by hand against `git diff` of `StatusPills.tsx`: no raw colors (grep for hex/`rgb(` outside `var(--...)` returned nothing), no corner-radius drift (no new rounded container added), no unmarked numeric data (the pill shows only a status label, no price/timestamp), no hand-rolled panel (reuses the existing `Pill` component), no new `Date`/UTC logic, no header/ticker/sidebar layout math touched, no duplicated JSX against an existing shared component. No findings.
- **Trailing stop table left untouched:** the plan explicitly scoped this out (Phase 3 owns those point values) even though the table is already known to be stale versus `instruments.py`'s current 25/55/80-point scalp trail — not re-verified or fixed here, flagged for whichever phase next touches that section.

## Deviations from Plan

None - plan executed exactly as written, aside from the ui-consistency-reviewer substitution above (a process deviation, not a code deviation, flagged for human confirmation per the same pattern established in 02-06).

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. No new env keys; `ENABLE_TICK_FEED` remains off by default.

## Next Phase Readiness

- ORD-04 is closed: tick-driven stop triggering's degradation to the 20-second fallback is now proven by a test (with the tick feed provably forced down, per Research Pitfall 1), documented in the Strategy Guide, and visible on the dashboard — phase success criterion 4.
- This was the final plan in Phase 2 (Order Placing & Tracking). ORD-01 through ORD-04 are all closed; the phase's own gate (`python -m pytest -q` full suite, `ruff check index_ai/` no new errors) was run as part of this plan: **765 passed** (up from the 760 baseline after plan 02-06, exactly the 5 new tests added here — 3 named tests, one of them parametrized ×3), `ruff check index_ai/` shows 7 pre-existing cosmetic errors, none new (no `index_ai/` files were touched by this plan).
- **A human should load the dashboard once** (`python -m uvicorn index_ai.server:app --port 8000`) to visually confirm the Ticks pill renders and its tooltip reads correctly — this plan verified the build (`npm --prefix dashboard run build`, 0 `error TS`) and applied the ui-consistency-reviewer checklist manually, but did not load the running page (per the plan's own human-check note in Task 3's `<verify>`).
- Phase 3 (exit optimisation, per the roadmap) can proceed; it inherits a documented, dashboard-visible tick-feed fallback and should not need to touch `index_ai/scanner.py`'s trail/stop mechanics introduced here.

---
*Phase: 02-order-placing-tracking*
*Completed: 2026-10-01*

## Self-Check: PASSED

All claimed files found on disk (`tests/test_fast_trail_loop.py`, `guides/Strategy Guide.md`,
`dashboard/src/components/shell/StatusPills.tsx`, this SUMMARY). All three task commits
(`2060ad8`, `1eee06d`, `023d48c`) found in `git log`. Full repo suite: 765 passed (up from the
pre-plan baseline of 760, exactly the 5 new tests added here). `ruff check index_ai/`: 7
pre-existing errors, none new. `npm --prefix dashboard run build`: 0 `error TS` lines.
