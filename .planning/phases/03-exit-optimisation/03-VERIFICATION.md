---
phase: 03-exit-optimisation
verified: 2026-10-03T00:00:00Z
status: passed
score: 3/3 roadmap success criteria verified; human check passed (Richard, 2026-10-03, after server restart)
behavior_unverified: 0
overrides_applied: 0
re_verification: false
human_verification:
  - test: "Restart the server (python -m uvicorn index_ai.server:app --port 8000), rebuild the dashboard if needed (npm --prefix dashboard run build), open the Strategy P&L tab and look at the 'Stop check' panel; press 'Re-check now'."
    expected: "Eight rows (NIFTY/BANKNIFTY/SENSEX buy and sell, crypto, commodities), each with its stop, a state pill, trades/days/win/stop-closed/net in mono numbers and a plain 'Not enough data yet...' message; a spinner then a 'Stop check done' toast and a refreshed 'last run' time; no 'changed' flag today; footer says nothing here changes a stop."
    why_human: "No JS test runner exists; the running server has not been restarted, so GET /api/exit-recheck and POST /api/exit-recheck/run are not live yet. Visual layout and token consistency cannot be grepped."
warnings:
  - item: "dashboard/src/lib/strategies.ts:202 (Index Options long description) still says sells have 'the exit trailing the option premium itself'. This is the old percent-of-premium wording; the real rule is the 1:1 index-point trail. Only the one-line `engine` tag (line 196) was fixed by 03-06."
    severity: WARNING (user-facing copy only; no code path affected)
    fix: "Reword to 'the exit trailing the index in points (1:1)'. Needs npm run build afterwards."
---

# Phase 3: Exit Optimisation - Verification Report

**Phase Goal:** Trailing-stop tuning is kept current against live data instead of frozen at a one-time 2026-09-24/28 tune, and the codebase has exactly one trailing-stop implementation, not two contradictory ones.
**Verified:** 2026-10-03
**Status:** human_needed
**Re-verification:** No - initial verification

## Observable Truths (ROADMAP success criteria are the contract)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Current trailing-stop values for every index and both lanes re-checked against recent live-journal data, shown in rupees / win rate, with a repeatable re-check | VERIFIED | `index_ai/exit_recheck.py` (841 lines, substantive). Ran `compute_segments(None)` on a read-only backup copy of the real `memory/trade_memory.sqlite`: all six India segments plus crypto and commodities returned real rows. Examples: NIFTY buy 25-pt 3 trades, win 33%, net -1205.4; NIFTY sell 40-pt 10 trades, win 30%, stop-closed 90%, net -1706.5; BANKNIFTY sell 100-pt 10 trades, net -5.2; crypto 49 trades; commodities 27 trades, net +1780. Verdict `not_enough_data` for all, correct under the 40-trade / 15-day ladder (imported from `strategy_learning`, not copied). Repeatable: `run_recheck()` behind `GET /api/exit-recheck` and `POST /api/exit-recheck/run` (server.py 1598-1616, both `asyncio.to_thread`, POST takes no body), daily EOD step (`daily_ops.py` 236-241, failure contained), dashboard button. |
| 2 | `premium_trail.py` no longer exists and nothing imports it | VERIFIED | `index_ai/premium_trail.py` absent; `importlib.util.find_spec('index_ai.premium_trail')` returns None. Case-insensitive grep across `index_ai`, `dashboard/src`, `tests`, guides, CLAUDE.md (excluding stale `.claude/worktrees`, `.planning`, generated graph) finds only the regression test `tests/test_premium_trail_removed.py` and one CLAUDE.md sentence saying it was deleted. The only trailing logic left is `trailing.py` (1:1 index points) plus the rupee `profit_trail.py` fallback (kept deliberately, D-05). |
| 3 | A monitoring signal fires if trail-stop hit rate or win rate drifts from the validated baseline | VERIFIED | `_track_drift` / `_moved_more_than` (exit_recheck.py 733-790): baseline captured when a segment first becomes ready under a given distance; drift = more than 15 points on stop-hit rate OR win rate, computed with `fractions.Fraction` (exactly 15.0 is not drift); nothing can drift below the ladder bar; alert only on persisted OK to DRIFT transition, stored before sending, via `notify.alert` with a per-baseline key, in try/except. Dashboard shows "results changed" flag with was -> now. Tests: `test_drift_threshold_exact`, `_below_bar`, `_alert_once`, `_on_win_rate_alone`, `_alert_text_plain`, `_state_is_stored_before_the_alert...`, `test_run_eod_hook`, `_failure_is_contained`. |
| 4 | (Plan truth, the trap) Sell-lane credit spreads on NIFTY/BANKNIFTY/SENSEX still ignore flip/regime/EMA exits after the deletion; FINNIFTY/no-instrument still close on them | VERIFIED | `position_exits._index_trailed_credit` now keys on `SELL_TRAIL_POINTS` (line 32-37), handles nested instrument, case, spaces. Tests `test_index_trailed_credit_suppresses_...` (parametrised 3 indices + padded lowercase, nested/top-level, all 4 actions) and `test_credit_spread_without_a_sell_trail_still_closes` pass. Iron condor unaffected. |
| 5 | (Plan truth) Buys unchanged; rupee profit trail still reached for a buy with an MTM figure | VERIFIED | `test_rupee_profit_trail_is_still_called_for_a_buy_with_an_mtm_figure` passes; buy 25/55/80 and sell 40/100/130 in code match the Strategy Guide table (checked by script output vs guide text). |
| 6 | (Plan truth) Suggestions are suggest-only, never applied; ladder -> frozen -> replay quality -> net rupees gate; replay_unreliable is an accepted outcome | VERIFIED | `_verdict` / `_india_replay_gate`: order not_enough_data, frozen (before any replay), replay quality (match >= 0.8, >= 40 priceable), MIN_EXTRA_RUPEES 500. No code path writes a stop setting; text ends "Nothing was changed". `market_log` opened only through a `mode=ro` URI helper. |
| 7 | (Plan truth) Overlapping runs do not interleave | VERIFIED | `_RECHECK_LOCK` non-blocking acquire in `run_recheck`; a waiter returns the stored result. State is one `learned_settings` row (`exit_recheck_state`), written whole (absolute values). |
| 8 | (Plan truth) Dashboard panel exists, uses existing primitives, builds and type-checks | VERIFIED (code) / human for visual | `ExitRecheckPanel` in `StrategyPerformancePage.tsx` (line 116, mounted line 588), `useExitRecheck.ts` hook; `npx tsc --noEmit -p dashboard/tsconfig.app.json` exit 0. Real rendering needs human check below. |

**Score:** 3/3 roadmap criteria verified at code level; 0 behavior-unverified (the behavior-dependent truths 4, 6, 7 and the drift alert each have a passing named test).

### Requirements Coverage

| Requirement | Source Plans | Status | Evidence |
|-------------|--------------|--------|----------|
| EXIT-01 re-validate trail values on a recurring basis | 03-01, 03-04, 03-05, 03-06 | SATISFIED | Truth 1 and 6; daily EOD hook plus button. |
| EXIT-02 dead percent-of-premium path removed, one implementation | 03-02, 03-03, 03-06 | SATISFIED (one copy-text warning) | Truth 2, 4, 5; guide, CLAUDE.md, comments reworded. Stale sentence at `dashboard/src/lib/strategies.ts:202` is the only leftover. |
| EXIT-03 monitoring signal on drift | 03-01, 03-05, 03-06 | SATISFIED | Truth 3. |

All three IDs appear in PLAN frontmatter (03-01..03-06) and in REQUIREMENTS.md (Phase 3). No orphaned requirements. (REQUIREMENTS.md checkboxes / traceability table still say Pending - the orchestrator should flip them.)

### Behavioral Spot-Checks and Tests

| Check | Command | Result | Status |
|-------|---------|--------|--------|
| Phase 3 test files | `pytest -q tests/test_exit_recheck.py tests/test_premium_trail_removed.py tests/test_position_exits.py tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_profit_trail.py tests/test_credit_spread.py` | 103 passed in 5.4 s | PASS |
| Real journal, read-only copy | `compute_segments(None)` on a `sqlite3` backup copy | 8 segments, 0 errors, plausible real numbers | PASS |
| Module gone | `find_spec('index_ai.premium_trail')` | None | PASS |
| Types | `tsc --noEmit -p dashboard/tsconfig.app.json` | exit 0 | PASS |
| Lint | `ruff check index_ai/` | 7 errors (pre-existing baseline of about 8) | PASS |
| Full suite | Not re-run (per brief: 837 passed; 1 known weekend-only unrelated failure `test_commodities.py::test_lane_plumbing_open_then_close`) | - | Accepted from brief |

Probes (Step 7c): none declared. Debt markers (TBD/FIXME/XXX) not found in the new module by the targeted scans; no stub patterns (module is real logic with 47 tests).

### Anti-Patterns

| File | Line | Pattern | Severity |
|------|------|---------|----------|
| `dashboard/src/lib/strategies.ts` | 202 | Stale copy: "exit trailing the option premium itself" | WARNING (text only) |

### Human Verification Required

1. **Stop check panel after restart.** Restart the server and rebuild the dashboard, open Strategy P&L, check the "Stop check" panel and press "Re-check now". Expected: eight plain-language rows with "Not enough data yet" messages, spinner then toast, refreshed last-run time, no "changed" flag, footer saying nothing changes a stop. Why human: no JS test runner; new endpoints are not live on the running server; visual consistency is not greppable.

### Gaps Summary

No blocking gaps. Goal is achieved in code: one trailing-stop implementation (1:1 index points, plus the intentionally retained rupee profit-trail fallback), a repeatable and automated re-check against the real journals, and a persisted drift baseline with a once-per-event Telegram alert. First real output is `not_enough_data` for every segment (3-10 India trades each), which is the designed, honest answer under the 40-trade / 15-day ladder, so no stop change or suggestion is expected yet. The suggestion/replay path has only been exercised by unit tests, not by ready real data. One cosmetic warning: the Index Options long description still mentions trailing the option premium.

---

_Verified: 2026-10-03_
_Verifier: Claude (gsd-verifier)_
