---
gsd_state_version: 1.0
current_phase: 03
current_phase_name: Exit Optimisation
status: executing
stopped_at: Completed 03-03-PLAN.md
last_updated: "2026-10-03T08:43:34.248Z"
last_activity: 2026-10-03
last_activity_desc: Phase 03 execution started
state_head: dc414091972b093248379d30f43901e119489bfa
progress:
  total_phases: 10
  completed_phases: 2
  total_plans: 17
  completed_plans: 14
  percent: 20
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-30)

**Core value:** Never presents a strategy as ready for real money until it has been measured — against real broker charges, on the real live journal, not a backtest — to actually make money.
**Current focus:** Phase 03 — Exit Optimisation

## Current Position

Phase: 03 (Exit Optimisation) — EXECUTING
Plan: 4 of 6
Status: Ready to execute
Last activity: 2026-10-03 — Phase 03 execution started

Progress: [██░░░░░░░░] 20%

## Performance Metrics

**Velocity:**

- Total plans completed: 11
- Average duration: - min
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 4 | - | - |
| 2 | 7 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 35 min | 2 tasks | 5 files |
| Phase 01-strategy-fixes P02 | 25 min | 2 tasks | 4 files |
| Phase 01-strategy-fixes P03 | 25 min | 3 tasks | 6 files |
| Phase 01 P04 | 10 min | 1 tasks | 1 files |
| Phase 02 P01 | 45 min | 2 tasks | 9 files |
| Phase 02 P02 | 55 min | 2 tasks | 3 files |
| Phase 02-order-placing-tracking P03 | 55 min | 3 tasks | 6 files |
| Phase 02 P04 | 22 min | 2 tasks | 5 files |
| Phase 02-order-placing-tracking P05 | 38min | 2 tasks | 3 files |
| Phase 02 P06 | 35min | 2 tasks | 5 files |
| Phase 02 P07 | ~25min | 3 tasks | 3 files |
| Phase 03 P01 | 9 min | 2 tasks | 3 files |
| Phase 03 P02 | 9 min | 3 tasks | 7 files |
| Phase 03 P03 | 10 min | 2 tasks | 9 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: Phases derived directly from REQUIREMENTS.md categories (ISOL, BROK, SAFE, REPT, BILL, COMP) rather than the research summary's 8-phase infra breakdown — collapsed to 6 phases per standard granularity, since several research phases (auth, containerization, control plane) are implementation means to these requirement-driven phases, not separate requirement-bearing phases themselves.
- Roadmap: Phase order follows the dependency chain research flagged — tenant isolation first (everything else needs "per-subscriber" to be real), then credentials, then safety locks, then reporting, then billing (needs only subscriber identity), then the compliance gate last since it's dated/blocking rather than a normal build step.
- Roadmap (2026-09-30, same day): Richard asked to add strategy fixes, order placing/tracking, exit optimisation, and dashboard UI/UX. Grounded these in `.planning/codebase/CONCERNS.md`'s already-documented gaps rather than inventing new scope. Placed as Phases 1-4, ahead of the subscription build-out — get the product itself right before building the machinery to sell it — with the subscription phases renumbered 5-10. Phases 1-4 have no dependencies on each other or on Phase 5, so could run in parallel if preferred; flagged in ROADMAP.md, not assumed.
- [Phase 1]: Buy-lane-only CPR-direction gate (BUY_BLOCK_CONTRA_CPR), default off; global cpr_narrow/wide width thresholds untouched since they also drive the frozen, net-positive NIFTY sell lane — D-05/D-10 and the confidence ladder: never touch a frozen strategy, never let a tuning change reach real money by default
- [Phase 1]: strategy_lab's two new live-buy-lane candidates are ON_DEMAND-excluded from run()'s default name set — keeps GET /api/strategy-lab's per-call cost unchanged; proven by a test that makes the adapter raise if called during a default run
- [Phase 1]: Buy-lane viability rows removed outright from OBSERVED_GROSS_PER_TRADE (not flagged/zeroed) so no code path can hand out a fabricated buy-lane rupee figure; UNMEASURED is now the only reachable verdict unless gross is explicitly supplied — STRAT-03 required the proxy backtest numbers stop being presented as measured; deletion is the only guarantee against reintroduction
- [Phase 1]: strategy_scorecard/_india_rows since is keyword-only with a no-op default and no endpoint exposes it (server.py untouched) — Keeps the cut-off an internal measuring instrument, not a public promotion path, per the plan's STRAT-02 prohibition
- [Phase 1]: OI-wall room gate (BUY_BLOCK_INTO_OI_WALL, default off) added symmetrically to the buy lane; WALL_ROOM_PCT=0.10% chosen to match each index's own buy-trail distance (NIFTY 25 / BANKNIFTY 55 / SENSEX 80 points)
- [Phase 1]: strategy_lab.LIVE_BUY_TUNED now carries the full bundle (CPR-direction gate + OI-wall gate + 3 confirmed breakout closes); real 3-index chain replay shows NIFTY and BANKNIFTY worse on both win rate and net rupees, SENSEX better on net despite lower win rate -- recommendation to plan 01-04 is Hold, sample too thin (6-9 trades/index) and the two switches weren't isolated from each other
- [Phase 1]: [Phase 1] Richard's decision on buy-lane tightening (01-03's tuned bundle): Hold -- NIFTY and BANKNIFTY got worse on win rate and net rupees over ~1 week real-chain data, SENSEX improved but the two switches weren't isolated from each other in that run; neither BUY_BLOCK_CONTRA_CPR nor BUY_BLOCK_INTO_OI_WALL goes on for paper trading
- [Phase 2]: Crypto lost-entry settlement: setup_live_crypto_lane fixes on ny_n_break/BTCUSD for a deterministic live-lane test harness reused by plan 02-02
- [Phase 2]: Crypto lost-entry settlement: Delta-unreachable simulated via scripted HTTP 500 rather than queued transport faults, avoiding httpx's internal GET-retry loop needing multiple fault entries
- [Phase 2]: Crypto Close-all/Close now read open keys under _STATE_LOCK so a position mid-placement is never missed (ORD-01); no new lock introduced
- [Phase 2]: A Delta outage on a held crypto position is held and escalated after 180s (UNCLEAR_ALERT_SECONDS) instead of being read as closed; reconcile retries next scan instead of marking the day done when unreadable (ORD-02)
- [Phase 2]: 02-03: Dhan correlationId field name confirmed against real captured traffic before coding find_order_by_correlation -- no assumption risk carried into ORD-01/ORD-02 India settlement
- [Phase 2]: 02-03: place_live_entry_orders' alert+re-raise wrapping covers the immediate order_response_ok rejection check as well as the two confirmation calls, since the plan's own behavior spec requires an alert for a hedge-accepted/short-rejected partial entry
- [Phase 2]: 02-03: _EXIT_ATTEMPTED is a bare in-process set (no persistence) -- documented ceiling is a server restart forgets it; upgrade path is persisting the flag on the trade row
- [Phase 2]: 02-04: settle_pending_entry cancels a still-working India entry under the per-instrument lock, re-reading Dhan's order book strictly after the cancel attempt (never trusting the DELETE reply) to decide CANCELLED vs LIVE_TRADED vs UNRESOLVED
- [Phase 2]: 02-04: wait_for_inflight_entries adds no new lock -- it snapshots and acquires/releases the existing per-instrument locks so India Close-all waits for an in-flight entry placement before reading open_trades()
- [Phase 2]: 02-05: sync_open_live_trades now lists trades before any broker call and builds all three Dhan indexes with strict=True inside one try, so a disconnect aborts before touching a row instead of reading swallow-to-{} as Dhan showing nothing (ORD-02, D-07)
- [Phase 2]: 02-05: reconcile() removed its early no-open-live-trades return so an untracked Dhan position (ORPHAN_BROKER) is caught even with nothing open; a failed strict position read now aborts with ok=False + one alert instead of reading as every position gone
- [Phase 2]: 02-06: last_error is no longer reset to None on every successful connect — a reconnect after a disconnect packet was wiping the disconnect message before it could be observed; it is now genuinely the last error, not a live status flag
- [Phase 2]: 02-06: run_feed's receive loop wrapped in try/finally so the buffer (which lives across reconnects) is flushed on every exit path — a hard drop or a shutdown during an outage no longer strands received ticks
- [Phase 2]: 02-06: a server disconnect packet now leaves the receive loop itself (not just the inner for-loop), so the feed reconnects at once via existing backoff instead of waiting out the 90s stall timer
- [Phase 2]: [Phase 2]: 02-07: tested the ORD-04 fallback with the tick feed provably forced down (connected=False, stale last_tick_at) rather than relying on ENABLE_TICK_FEED alone -- proves a crossed stop still closes on the 20-second Dhan price check
- [Phase 2]: [Phase 2]: 02-07: ui-consistency-reviewer's 7-point checklist applied manually to StatusPills.tsx (no Task/Agent tool in this execution context) -- no findings, documented in the SUMMARY for human confirmation
- [Phase 2]: Mid-phase live incident (not part of the planned work): btc_daily_straddle's 2-leg position has no "side" key, crashing crypto/lanes.py's auto-prune loop every ~70s for 2+ hours once the straddle actually opened a position on 2026-10-01 -- fixed by adding it to _known_strategies(); server restarted to pick up the fix
- [Phase 2]: Phase code review (02-REVIEW.md) then found the SAME gap still open on the manual Close/Close-all dashboard controls (CR-01) and a second, independent critical bug in Delta's client misclassifying a non-JSON 5xx response as a definite order rejection instead of unknown, skipping the settle-from-order-book path (CR-02) -- both fixed, plus 4 warnings (ALREADY_CLOSED mislabeled as BLOCKED, a lazy-lock barrier gap, a stale fixture README, a leaked IP in a committed fixture), each with a regression test, before the phase was marked complete
- [Phase 03]: 03-01: re-check ladder bar is strategy_learning's 40 trades and 15 trading days (imported), one day stricter than D-03's 14; segment = (lane, index), ids india_<INDEX>_<lane> — D-03 intent is the same bar used everywhere else; recorded visibly
- [Phase 03]: 03-02: sell-lane flip/regime/EMA exit suppression now keys on credit_spread.SELL_TRAIL_POINTS (NIFTY/BANKNIFTY/SENSEX, same strip/upper match) - pinned by a regression test that passed before and after the swap; premium_trail.py deleted, rupee profit_trail fallback kept (D-05)
- [Phase 03]: 03-03: comment/docstring/doc refresh is words-only; five money-path .py files proven AST-identical (docstrings stripped) vs the pre-plan commit; Strategy Guide trailing-stop numbers verified by script against SELL_TRAIL_POINTS and get_instrument(...).trail_distance_points

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 10 (Compliance & Go-Live Gate) depends on primary-source SEBI circular verification (or counsel) per research/SUMMARY.md — do not treat the static-IP architecture as sufficient compliance until that's resolved.
- Phase 9 (Billing) needs its own research pass on India payment gateway choice (Razorpay vs. Stripe India) before planning — not covered in the initial research pass.
- This machine's live .env already has ENTRY_CONFIRMATION_BARS=3 (not the code default of 2), discovered via the strategy_lab CLI's baseline readout -- worth confirming with Richard whether that's intentional before plan 01-04 treats the 01-03 sanity-check numbers as a clean baseline-vs-tuned comparison

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-10-03T08:43:33.843Z
Stopped at: Completed 03-03-PLAN.md
Resume file: None
