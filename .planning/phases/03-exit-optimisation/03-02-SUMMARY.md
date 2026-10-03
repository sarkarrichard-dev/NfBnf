---
phase: 03-exit-optimisation
plan: 02
subsystem: api
tags: [trailing-stop, exit-rules, deletion, regression-test, money-path]

requires:
  - phase: 03-exit-optimisation
    provides: "03-01 exit re-check tracer (classifier files old 'Hard stop: premium' notes under legacy_premium)"
provides:
  - "position_exits._index_trailed_credit keyed on credit_spread.SELL_TRAIL_POINTS (behaviour-identical replacement)"
  - "one trailing-stop implementation: index_ai/premium_trail.py and its tests deleted"
  - "regression pins: sell-lane flip/regime/EMA suppression and the removal tests"
affects: [03-03 comment/doc refresh, 03-06 dashboard wording]

actuals:
  tokens: 7100
  tasks: 3
  commits: 4

tech-stack:
  added: []
  patterns:
    - "Pin behaviour with a test on the old code, swap, re-run the same test (predicate swaps on the money path)"

key-files:
  created:
    - tests/test_premium_trail_removed.py
  modified:
    - index_ai/position_exits.py
    - index_ai/entry_guard.py
    - index_ai/trailing.py
    - tests/test_position_exits.py
  deleted:
    - index_ai/premium_trail.py
    - tests/test_premium_trail.py

key-decisions:
  - "Sell-lane exit suppression now keys on credit_spread.SELL_TRAIL_POINTS (same three indices, same strip/upper match); _TRAILED_VERTICALS unchanged"
  - "profit_trail fallback kept and now runs on every check with an MTM figure (D-05), no second profit mechanism added"

requirements-completed: []  # EXIT-02 spans several plans; the orchestrator marks it when the whole phase verifies

duration: 9min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 02: Remove the old percent-of-option-price trail Summary

**The old percent-of-option-price trail is deleted so the code has exactly one trailing stop (the 1:1 index-point trail), with the one live use of that module (the sell-lane "ignore flips" rule) first pinned by a regression test and re-keyed on `SELL_TRAIL_POINTS` with no change in behaviour.**

## Performance

- **Duration:** ~9 min (08:20Z to 08:29Z)
- **Tasks:** 3 of 3
- **Files:** 1 created, 4 modified, 2 deleted (plus this summary)

## Accomplishments

- Reachability re-proven and the old trail's history saved in git and in Claude's memory notes before anything was deleted (sections below).
- THE TRAP handled in the plan's order: the exit-suppression regression test passed on the old code, the predicate was swapped, and the same test passed again.
- `index_ai/premium_trail.py`, `tests/test_premium_trail.py` and the dead premium block in `trailing.evaluate_open_trade` are gone; the rupee `profit_trail` fallback is kept.

## Preserved history (D-04)

The old percent-of-option-price trail (`index_ai/premium_trail.py`) is deleted in this plan. Its history is kept here (tracked in git) and in Claude's auto-memory `strategy-findings.md` ("Premium trail (removed 2026-10, Phase 3)").

> Richard's spec 2026-08-31 for NIFTY/BANKNIFTY, SENSEX added 2026-09-15 with derived numbers (avg recorded SENSEX entry premium ~₹216; hard_stop_pts / entry-premium ratio ~16% NIFTY, ~20% BANKNIFTY, ~18% average -> hard_stop_pts=39; trail ~40% of hard stop). Config: NIFTY hard_stop 11 / first target 5% / trail 5; BANKNIFTY 100 / 5% / 35; SENSEX 39 / 5% / 16 (premium points). First target 2026-09-23: 5% of entry premium (was 25%). At 25% the trail almost never armed: every one of the 13 trades whose trail armed since 2026-09-10 closed green, every loser had an unarmed trail, winners like +₹1,014 rode to the full hard stop (-₹2,979). Replay of 37 real recorded MTM paths (gross): 25% -> -₹6,914, 7% -> -₹3,313, 5% -> -₹2,225. Superseded 2026-09-24/28 by 1:1 index-point trails (buys 25/55/80, sells 40/100/130).

Per-index configuration (premium points; first target is a share of the entry premium):

| Index | Hard stop | First target | Trail (bounce off best) | Who chose the numbers |
|---|---|---|---|---|
| NIFTY | 11 | 5% | 5 | Richard, 2026-08-31 |
| BANKNIFTY | 100 | 5% | 35 | Richard, 2026-08-31 |
| SENSEX | 39 | 5% | 16 | Derived, 2026-09-15 |

How SENSEX's numbers were derived (Richard had no hand-picked numbers for it): the ratio of hard stop to the average recorded entry premium is about 16% for NIFTY and about 20% for BANKNIFTY. SENSEX's own recorded average entry premium (real trades since the 2026-09-10 epoch, a thin sample) was about ₹216; applying the ~18% average of the two ratios gives a hard stop of 39. The trail follows the same ~40%-of-hard-stop ratio NIFTY and BANKNIFTY both land near (16). Flagged in the code as "a reasoned starting point, not Richard's own spec."

What survives the deletion: the sell lane's "skip the signal-flip / CPR-regime / EMA-cross exits for a directional credit spread" rule. It used to key on this module's enabled-check and now keys on `credit_spread.SELL_TRAIL_POINTS` (same three indices, same strip/upper normalisation) - see Task 2.

## Reachability evidence

Re-checked 2026-10-03 on today's code and the real journal, before any deletion.

1. **Emitter grep.** `grep -rn "SELL_ATM_PUT\|SELL_ATM_CALL" index_ai --include=*.py` outside `index_ai/strategies/options_cpr/`: hits only in `backtest.py`, `backtest_options.py` (backtest sets/helpers), `brain/features.py`, `ml_outcomes.py`, `planner.py`, `position_exits.py` (direction sets), `execution_safety.py` (validator, "must be PUT/CALL"), `strategies/credit_spread.py`, `strategies/option_structures.py` (leg builder keyed on the action), `strategies/premium_sell.py` (allowed-action set / validator) and `trailing.py` (direction maps). None of `sell_strategy.py`, `strategy_router.py`, `strategy.py`, `plan_builder.py` mention either name. No live signal emitter of a naked single-leg sell exists.
2. **Hedged-spread default.** `strategy_params.apex_use_hedged_spreads: bool = True` (`strategy_params.py:24`), read with default True from `APEX_USE_HEDGED_SPREADS` (`:143`).
3. **Real journal, opened read-only** (`memory/trade_memory.sqlite`, `Path.resolve().as_uri() + "?mode=ro"`, nothing written): 89 trades total, **0 open trades** (`pnl IS NULL`), **0 open trades carrying `pt_entry`**; 46 closed trades carry `pt_entry` in `option_json`, the **newest created 2026-09-24T13:47:01+05:30**. Matches RESEARCH exactly.
4. **Other importers.** grep of `premium_trail` over `index_ai/`, `tests/`, `dashboard/src`, `scripts/`, `crypto/`, `commodities/`: importers are only `position_exits.py` (the live predicate - replaced in Task 2), `trailing.py` (the dead premium block - removed in Task 3), the module's own test file, and prose in `entry_guard.py`. Nothing else the plan did not already list.

Conclusion: the percent-of-option-price branch in `trailing.evaluate_open_trade` is unreachable (buys excluded by `_is_long_premium`; every credit spread returns earlier into `evaluate_credit_open_trade`), no open trade carries its state. Safe to proceed. The position_exits predicate is the one live use and is handled first.

## Task Commits

1. **Task 1 (history + reachability)** - `63c659e` docs, committed before anything was deleted.
2. **Task 2 (TDD, the trap)** - test `bee0804` (pins the behaviour on the OLD code), refactor `aa13bf7` (the predicate swap).
3. **Task 3 (deletion)** - `1e7db60` (two files deleted, dead branch removed, removal tests added).

**Plan metadata:** docs commit follows this file.

## Before/after proof for the trap (Task 2)

- `tests/test_position_exits.py` (renamed `test_index_trailed_credit_ignores_signal_flip`, plus the new parametrized `test_index_trailed_credit_suppresses_flip_regime_and_ema_exits`, `test_credit_spread_without_a_sell_trail_still_closes`, `test_iron_condor_on_a_trailed_index_is_not_suppressed`): **33 passed** across `test_position_exits.py`, `test_position_exits_ema.py`, `test_exit_credit.py` on the pre-swap code (`bee0804`), and **33 passed** again after the swap (`aa13bf7`).
- The test does discriminate: with the suppression forced off, `test_index_trailed_credit_ignores_signal_flip` fails (probe run, nothing committed).
- What it covers: NIFTY / BANKNIFTY / SENSEX and `" nifty "` (lower case, padded), instrument at the top level or only inside `option`, for both `SELL_BEAR_CALL_SPREAD` and `SELL_BULL_PUT_SPREAD`; the opposing fresh signal, the opposing CPR regime and the opposing EMA cross (EMA exit switched on with `EXIT_CREDIT_ON_EMA_CROSS_FLIP=true`) all return `None`. FINNIFTY (top level and nested) and no instrument at all still return a closing reason for all three; an iron condor on NIFTY is still closed by the EMA cross and by the regime change.
- The fixture rebuilds the cached strategy params on the way out so no other test inherits the switched-on EMA setting.

## Files Created/Modified

- `index_ai/position_exits.py` - `_premium_trailed_credit` -> `_index_trailed_credit` (lazy import of `SELL_TRAIL_POINTS`, `inst.strip().upper() in SELL_TRAIL_POINTS`), comment and docstring reworded (no mention of the old rule, "quarter" gone)
- `index_ai/entry_guard.py` - docstring sentence only
- `index_ai/trailing.py` - comment block, lazy import, `pt_evaluated` and the premium if-block removed; `if mtm is not None:` keeps the `evaluate_profit_trail` call unchanged
- `index_ai/premium_trail.py`, `tests/test_premium_trail.py` - deleted
- `tests/test_position_exits.py` - rename + new regression tests
- `tests/test_premium_trail_removed.py` - module gone, name gone, `profit_trail` still reached (a spy returns `(meta, True, "Profit trail: test")` and the buy exits with that reason)
- Outside git: auto-memory `strategy-findings.md` (appended "Premium trail (removed 2026-10, Phase 3)") and `standard-trailing-stop-and-profit.md` (premium-trail bullet put in past tense, "live and working / wired into position_exits" text removed, profit_trail paragraph now says what the code does after this plan, D-05 sentence added). Both edited with Edit, frontmatter intact; the MEMORY.md one-line hooks are still accurate so were not touched.

## Decisions Made

- Suppression keyed on the live trail's own table (`SELL_TRAIL_POINTS`), so adding a fourth index to the sell trail later also adds it to the flip-exit skip automatically, and the two cannot drift apart.
- Instrument extraction kept exactly as before (top level first, then `option.instrument`); only the membership test changed.

## Deviations from Plan

None - plan executed exactly as written. Notes, not deviations:
- Task 3's acceptance line "ruff reports nothing new compared with `git stash`" was done as the orchestrator instructed (no stash): `ruff check index_ai/` is 7 errors before and after, none in a touched file.
- No Task 2/3 line numbers were stale in a way that mattered.

## Hand-applied reviewer checklists (no subagent tool; the orchestrator also runs the real agents)

**trading-safety-reviewer** (files touched: `position_exits.py`, `trailing.py`)
1. Blocking I/O on the event loop - not applicable. No `server.py` handler changed; the changed functions are pure and unchanged in their callers.
2. Read-modify-write races - none introduced. No shared state or lock touched (lock count unchanged).
3. Live-arming interlock - untouched. `config.py`, `executor.py`, `dhan_orders.py`, `exit.py`, `charges.py`, `risk_manager.py` not in the diff; `git diff` of the plan shows only the files listed above.
4. Cost model - untouched.
5. Order sequencing - untouched.
6. The real risk (finding, resolved): the one live use of the deleted module was `position_exits._premium_trailed_credit`. Handled by Task 2's pin-then-swap; the suppression set is identical (three keys, same normalisation). Residual, recorded not engineered for (as the plan flagged): a hypothetical naked single-leg sell would now fall to the rupee `profit_trail` instead of the old option-price trail; no emitter exists (Reachability evidence) and hedged spreads default on.
7. `trailing.update_trail`, `init_trail_meta`, the Supertrend handling, `_is_long_premium`, `credit_spread.evaluate_credit_open_trade` and `SELL_TRAIL_POINTS` / `_buy_scalp_trail` values are untouched; buy trail suites (`test_buy_scalp_trail`, `test_trailing`, `test_credit_spread`, `test_tick_driven_stops`, `test_fast_trail_loop`) green.

**strategy-tuning-reviewer**
1. No stop value, threshold or ladder constant changed; nothing here tunes a strategy.
2. Nothing frozen is altered: the net-positive NIFTY sell lane keeps exactly the same exits.
3. No backtest numbers used as proof; the proof is behavioural tests plus the live journal (read-only).
4. The preserved history keeps the replay figures labelled as gross and historical.

**test-isolation-reviewer** (files: `tests/test_position_exits.py`, `tests/test_premium_trail_removed.py`)
1. Nothing reached calls `index_ai.notify`; `strategy_exit_reason` and `evaluate_open_trade` are pure. No Telegram variable set or re-set.
2. No broker or HTTP path reached.
3. No real `.env`, journal or DB written. The fallback test uses `settings().risk` the same way `test_buy_scalp_trail.py` does (conftest redirects `.env`); the removal scan only reads source files. The fixture's env changes are scoped by `monkeypatch.context()` and the cached params are rebuilt afterwards.
4. `tests/conftest.py` not modified.

## Verification

- Task 3 command (11 test files): **96 passed**. Extra: `test_exit_recheck`, `test_day_review`, `test_supertrend_exit`, `test_entry_guard`: **24 passed**.
- `git ls-files index_ai/premium_trail.py tests/test_premium_trail.py` prints nothing; `importlib.util.find_spec('index_ai.premium_trail')` is None ("gone"); the identifier appears in no `.py/.ts/.tsx` under `index_ai/`, `tests/`, `dashboard/src` apart from the test that scans for it.
- `grep -n "if mtm is not None:" index_ai/trailing.py` matches at line 232 inside `evaluate_open_trade`; `grep -n pt_evaluated` prints nothing.
- Lock tripwire: `grep -rn "threading.Lock()\|threading.RLock()" index_ai crypto --include=*.py | wc -l` = **13** (unchanged).
- Ruff: `ruff check index_ai/` = **7** errors before and after (the same seven pre-existing ones); zero in `position_exits.py`, `entry_guard.py`, `trailing.py` or the two test files.
- Full test suite not run here (the orchestrator runs it after the wave).

## Issues Encountered

None.

## Known Stubs

None.

## Threat Flags

None - no new endpoint, auth path, file access or schema change. T-03-07 to T-03-10 mitigated as planned (regression test before and after, only the unreachable block removed with the fallback proven by a spy test, history committed before deletion, journal opened `mode=ro` only).

## Next Phase Readiness

Ready for 03-03 (comment-only prose and docs refresh), which can now reword the remaining mentions in planner.py / instruments.py / option_structures.py / credit_spread.py / test_credit_spread.py / docs. Note for 03-03: a repo-wide grep for the old name will also hit `.claude/worktrees/` and `graphify-out/` by design; both are outside the removal test's scan.

## Self-Check: PASSED

- Created and modified files exist on disk; `index_ai/premium_trail.py` is gone.
- Commits `63c659e`, `bee0804`, `aa13bf7`, `1e7db60` exist in `git log`, and the history commit is older than the deletion commit.
- Task 1, 2 and 3 acceptance criteria re-run and met (outputs above).

---
*Phase: 03-exit-optimisation*
*Completed: 2026-10-03*
