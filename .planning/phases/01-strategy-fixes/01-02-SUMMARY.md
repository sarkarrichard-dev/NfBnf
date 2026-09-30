---
phase: 01-strategy-fixes
plan: 02
subsystem: strategy-measurement
tags: [viability, scorecard, options_cpr, strategy_performance, black-scholes-proxy, measurement]

# Dependency graph
requires:
  - phase: 01-strategy-fixes (plan 01)
    provides: the tuned live buy lane and its CPR-direction gate this plan's since filter will judge
provides:
  - viability("NIFTY"|"BANKNIFTY"|"SENSEX", "buy") returns UNMEASURED with gross_per_trade_rupees None (no BS-proxy rupee figure presented as measured)
  - strategy_scorecard(since=...) / _india_rows(since=...) to judge the tuned NIFTY buy entries on only their own paper trades
affects: [strategy-fixes (plan 04, which will call strategy_scorecard(since=FLIP) once 40+ tuned-era trades exist), dashboard Strategy P&L tab (unaffected — still calls with no argument)]

# Actuals (#2632)
actuals:
  tokens: 2900
  tasks: 2
  commits: 4

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Optional since: str | None cut-off threaded only to the India side of a scorecard, computed as the lexical max of two IST ISO strings (data_epoch() and since) — reuses the existing _after_epoch lexical-prefix comparison rather than adding a new comparison mechanism"

key-files:
  created: []
  modified:
    - index_ai/strategies/options_cpr/viability.py
    - tests/test_options_cpr.py
    - index_ai/strategy_performance.py
    - tests/test_strategy_performance.py

key-decisions:
  - "Buy-lane rows deleted outright from OBSERVED_GROSS_PER_TRADE rather than kept-but-flagged, so there is no code path left that can hand a caller a fabricated buy-lane rupee number — UNMEASURED is the only possible verdict now unless a gross is explicitly supplied"
  - "since is a keyword-only cut-off with a no-op default; no endpoint or query parameter exposes it (server.py untouched), keeping it an internal measuring instrument, not a public knob — matches the plan's own prohibition against this being a promotion path"

requirements-completed: [STRAT-03, STRAT-01, STRAT-02]

coverage:
  - id: D1
    description: "Buy-lane viability reports UNMEASURED (no Black-Scholes-proxy rupee figure) for NIFTY, BANKNIFTY and SENSEX; sell lane unchanged"
    requirement: STRAT-03
    verification:
      - kind: unit
        ref: "tests/test_options_cpr.py#test_viability_blocks_structures_that_cannot_cover_their_costs"
        status: pass
    human_judgment: false
  - id: D2
    description: "strategy_scorecard(since=...) / _india_rows(since=...) isolate India trades at or after the later of the data epoch and since, leaving crypto/commodities and the no-argument call unchanged"
    requirement: STRAT-01
    verification:
      - kind: unit
        ref: "tests/test_strategy_performance.py#test_strategy_scorecard_since_isolates_tuned_buy_entries"
        status: pass
      - kind: unit
        ref: "tests/test_strategy_performance.py#test_crypto_rows_unaffected_by_since"
        status: pass
    human_judgment: false
  - id: D3
    description: "The since filter is a measuring instrument only — no caller declares the tuned buy entries ready for real money from this plan's work"
    requirement: STRAT-02
    verification: []
    human_judgment: true
    rationale: "Judgment call on whether the tuned entries have reached 40+ paper trades and clear D-06/D-07 belongs to plan 01-04 (or later), not this plan — this plan only builds the instrument"

duration: 25min
completed: 2026-09-30
status: complete
---

# Phase 1 Plan 2: Retire the proxy buy-lane edge, add a since cut-off to the scorecard Summary

**Removed the 2026-08-29 Black-Scholes-proxy buy-lane rupee figures from `viability.py` (now UNMEASURED) and gave `strategy_scorecard`/`_india_rows` an optional `since` cut-off so the tuned NIFTY buy entries can be measured on only their own paper trades.**

## Performance

- **Duration:** 25 min
- **Started:** 2026-09-30T07:05:00Z (approx)
- **Completed:** 2026-09-30T07:30:00Z (approx)
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- `OBSERVED_GROSS_PER_TRADE` no longer carries buy-lane rows for any of the three indices — `viability(index, "buy")` always returns `UNMEASURED` with `gross_per_trade_rupees is None` unless a gross is explicitly supplied, closing the path that let a 2026-08-29 backtest number be presented as a measured edge in the EOD report / AI day review.
- `strategy_scorecard(since=...)` and `_india_rows(since=...)` let the tuned NIFTY buy entries (from plan 01-01) be scored on only their own trades — the cut-off is the later of `data_epoch()` and `since`, so a `since` earlier than the epoch can never widen the window.
- Sell-lane viability, `entry_guard.py`, `daily_ops.py`, crypto/commodities scorecard rows, `crypto_live_readiness()` and `crypto_live_pairs()` are all untouched — verified by acceptance-criteria `git show --stat` checks per task and by the full 677-test suite.

## Task Commits

Each task was committed atomically (test then feat, per `tdd="true"`):

1. **Task 1: Retire the Black-Scholes-proxy buy-lane edge from viability.py (STRAT-03)** - `b5854a0` (test), `f02c7ad` (feat)
2. **Task 2: Scorecard cut-off so the tuned buy entries are judged on their own trades (D-06, D-07)** - `dd5cf54` (test), `d1d024f` (feat)

**Plan metadata:** committed separately after this SUMMARY (see below).

## Files Created/Modified
- `index_ai/strategies/options_cpr/viability.py` - Buy-lane rows removed from `OBSERVED_GROSS_PER_TRADE`; docstring, dict comment and `__main__` self-check updated to match
- `tests/test_options_cpr.py` - Buy-lane assertion changed from `NOT_VIABLE` to a per-index `UNMEASURED` + `gross_per_trade_rupees is None` loop, plus an explicit-gross override assertion
- `index_ai/strategy_performance.py` - `_india_rows(since=None)` and `strategy_scorecard(since=None)` added; cut-off is `max()` of the non-empty (`data_epoch()`, `since`) IST ISO strings
- `tests/test_strategy_performance.py` - Three new behaviors covered: since excludes pre-switch trades, epoch still wins over an earlier since, crypto rows identical with/without since

## Decisions Made
- Deleted the buy-lane dict entries outright rather than leaving them commented out or zeroed, so `viability()`'s existing `gross is None -> UNMEASURED` branch is the only code path a caller can reach — no risk of a stale constant being reintroduced by mistake.
- Kept `since` keyword-only with a no-op default and did not touch `server.py` — the dashboard's Strategy P&L tab keeps calling `strategy_scorecard()` with no argument, unaffected; `since` is reachable only from Python callers (e.g. a future plan-01-04 measurement script), matching the plan's explicit prohibition that this filter must not itself be read as a promotion signal.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `strategy_scorecard(since=FLIP)` is ready for plan 01-04 (or whichever later plan judges the tuned buy entries) once the flip timestamp from plan 01-01 and 40+ tuned-era paper trades exist — this plan built the instrument, it does not itself declare the entries ready.
- The buy lane's viability is now honestly `UNMEASURED` everywhere it's surfaced (EOD report, AI day review); the next real number comes from `scripts/measure_viability_gross.py` (once the live buy lane clears ~30 forward trades/index) or the Strategy Lab `live_buy_lane` candidate (plan 01-03's territory) — no blockers for either.

---
*Phase: 01-strategy-fixes*
*Completed: 2026-09-30*

## Self-Check: PASSED

All 4 modified files found on disk; all 4 task commits (b5854a0, f02c7ad, dd5cf54, d1d024f) found in git log. All plan-level `<verification>` commands re-run clean: `pytest tests/test_options_cpr.py tests/test_strategy_performance.py tests/test_strategy_learning.py tests/test_daily_ops.py -q` (31 passed), wave-end `pytest -q` (677 passed), `ruff check index_ai/strategies/options_cpr/viability.py index_ai/strategy_performance.py` (All checks passed!).
