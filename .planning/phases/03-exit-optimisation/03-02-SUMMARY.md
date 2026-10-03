---
phase: 03-exit-optimisation
plan: 02
subsystem: api
tags: [trailing-stop, exit-rules, deletion, regression-test]
status: in-progress
---

# Phase 3 Plan 02: Remove the old percent-of-option-price trail Summary

(In progress. Task 1 section below is complete; the rest of this file is completed after Task 3.)

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

<!-- gsd:write-continue -->
