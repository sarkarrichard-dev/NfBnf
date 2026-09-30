---
phase: 01-strategy-fixes
verified: 2026-09-30T12:52:38Z
status: passed
score: 5/6 must-haves verified (1 intentionally not-yet-met by human decision, see below)
behavior_unverified: 0
overrides_applied: 0
human_verification:

  - test: "Decide whether to run a switch-on trial of the tightened buy-lane gates (BUY_BLOCK_CONTRA_CPR / BUY_BLOCK_INTO_OI_WALL) now that the plumbing, tests and real-chain sanity check all exist"
    expected: "Richard either (a) stays on Hold, in which case STRAT-01 / roadmap Success Criterion 1 (win rate moved up) will never be met by this phase's artifacts alone and a follow-up phase/plan is needed, or (b) chooses to run the trial (full bundle / CPR-only), after which the 40-trade / 65%-win / net-positive bar recorded in 01-04-SUMMARY.md can be checked with `strategy_scorecard(since=FLIP)`"
    why_human: "This is explicitly a human trading-risk decision, not a code defect — plan 01-04 built the decision checkpoint on purpose and Richard already chose Hold once (2026-09-30), citing NIFTY/BANKNIFTY getting worse on both win rate and net rupees in the one-week real-chain sanity check. Whether to revisit is a judgment call for Richard, not something a verifier can pass/fail."
---

# Phase 1: Strategy Fixes Verification Report

**Phase Goal:** The option buy lane's real, live-journal win rate moves toward Richard's ≥65% precision bar, and every strategy-parameter change stays on the existing confidence ladder rather than being shortcut.
**Verified:** 2026-09-30T12:52:38Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Important framing (read before the tables below)

The launching instructions for this verification flagged, up front, that **STRAT-01 was deliberately left incomplete** by plan 01-04's own executor — not missed, not botched. Plan 01-03's real-chain sanity check (replaying the live buy lane's own entry logic against the real recorded option chain, 2026-09-23 through 2026-09-30, all three indices) showed the tightened rules came out **worse on both win rate and net rupees for NIFTY and BANKNIFTY** (only SENSEX improved, and the two new gates weren't isolated from each other in that run, so even the SENSEX result can't be attributed to either lever specifically). Richard reviewed that evidence and explicitly chose **"Hold — don't switch on yet."**

Given that, the live buy lane's real win rate has **not moved** from baseline — that is the correct, expected, and honestly-reported outcome of a human judgment call the phase's own process (the confidence ladder + a blocking decision checkpoint) was designed to produce. This verification does **not** treat "win rate hasn't moved" as a gap in the code or process. It is reported as a human-decision item (`human_needed`), not `gaps_found`.

Everything else this phase needed to build — the gates, the off-by-default switches, the real-data replay tooling, the retired stale numbers, the scorecard measuring instrument, the dashboard visibility, and the recorded decision itself — was checked directly against the codebase, not taken from SUMMARY.md claims.

## Goal Achievement

### Roadmap Success Criteria (the contract)

| # | Success Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Buy lane's measured live win rate has moved up from baseline, reported in rupees and win-rate points | **Not met — by design, human decision pending** | `.planning/phases/01-strategy-fixes/01-04-SUMMARY.md`: Richard's recorded Hold decision. Fresh baseline re-measured today (`strategy_scorecard()`): NIFTY 14 trades/3 wins/21.4%/−Rs6,034.30, BANKNIFTY 9/2/22.2%/−Rs7,000.50, SENSEX 4/2/50.0%/+Rs1,297.84 — unchanged mechanism, nothing switched on. Not a code/process gap; see framing above. |
| 2 | No strategy-parameter change ships without going through the 15-trade observe-only / 40-trade human-approved-suggestion ladder, even under time pressure | ✓ VERIFIED | `index_ai/strategy_learning.py` ladder thresholds unchanged (`WATCH_MAX=15`, `OBSERVE_MAX=40`, `READY_MIN_DAYS=15`) — file untouched by any of this phase's 9 commits (confirmed via `git show --stat` on every commit). Both new buy-lane switches (`BUY_BLOCK_CONTRA_CPR`, `BUY_BLOCK_INTO_OI_WALL`) default `False` in the dataclass and the `.env` loader; nothing auto-applies them; Richard made the human decision explicitly (Hold) via a blocking checkpoint task (01-04 Task 1, `type="checkpoint:decision" gate="blocking"`). No `.env` was edited, no server restart happened (Task 2 correctly skipped on Hold). |
| 3 | Every Strategy Lab verdict dated before 2026-09-23 is either re-validated against real chain data or explicitly flagged as pre-cutover/untrustworthy | ✓ VERIFIED | `index_ai/strategies/options_cpr/viability.py`: the three buy-lane `OBSERVED_GROSS_PER_TRADE` rows (the only pre-cutover Black-Scholes-proxy numbers in active use) are deleted; `viability(idx, "buy")` returns `UNMEASURED` / `gross_per_trade_rupees is None` for all three indices (confirmed live: `python -c "from index_ai.strategies.options_cpr.viability import viability; ..."`). `index_ai/strategy_lab.py` docstring (line 19) states every verdict is recomputed from `market_log.chain` rows starting 2026-09-23 — no pre-cutover Strategy Lab verdict exists to begin with, so there is nothing else to flag. |

**Score:** 2/3 roadmap criteria fully verified as code/process outcomes; the third is not a code defect but an intentionally-unresolved human decision, tracked as a human-verification item.

### Plan-Level Must-Haves (mechanics actually built)

| # | Truth | Status | Evidence |
|---|---|---|---|
| 1 | `buy_block_contra_cpr` switch exists, default off, wired into both direction branches | ✓ VERIFIED | `strategy_params.py:86,190`; `buy_strategy.py:153,198` (`cpr_contra_filter`, 2 occurrences, symmetric) |
| 2 | `buy_block_into_oi_wall` switch exists, default off, wired into both direction branches, `WALL_ROOM_PCT=0.10` | ✓ VERIFIED | `strategy_params.py:90,191`; `buy_strategy.py:23,165,174,210,219` (`oi_wall_room_filter`, 2 occurrences, symmetric) |
| 3 | Strategy Lab replay adapter (`_prev_day`, `_live_buy_read`) calls the real `evaluate_buy_signal`, no look-ahead, degrades safely on missing data, stays out of the dashboard's default `run()` | ✓ VERIFIED | `strategy_lab.py:458` (`evaluate_buy_signal(...)` call), `:625` (`names = ... if names else [n for n in CANDIDATES if n not in ON_DEMAND]`), `:213` (`ON_DEMAND = frozenset({"live_buy_lane", "live_buy_lane_tuned"})`); tests `test_live_buy_lane_replays_the_real_buy_signal`, `test_live_buy_lane_missing_prior_day_returns_no_trades`, `test_live_buy_read_no_look_ahead_and_builds_correct_inputs`, `test_default_run_never_evaluates_live_buy_candidates` all pass |
| 4 | `python -m index_ai.strategy_lab [INDEX]` CLI runs against real recorded data and produces baseline/tuned rows | ✓ VERIFIED — re-ran live | `python -m index_ai.strategy_lab NIFTY` executed directly by this verifier (not just trusted from SUMMARY): output matches 01-01-SUMMARY.md's numbers exactly (8 baseline trades/Rs1,895.19 net, 6 tuned trades/Rs233.80 net — the 6-trade tuned figure reflects the full 01-03 bundle since this machine's live `.env` already has `ENTRY_CONFIRMATION_BARS=3`, consistent with 01-03-SUMMARY's own noted discrepancy) |
| 5 | Buy-lane viability numbers retired to `UNMEASURED`; sell-lane numbers (real-journal, not proxy) untouched | ✓ VERIFIED | `viability.py:64-67` — 3 sell rows remain (`NIFTY sell: 6.0`, `BANKNIFTY sell: -178.0`, `SENSEX sell: -57.0`), 0 buy rows; `grep -c '"sell"): '` = 3, `grep -c '"buy"): '` = 0 |
| 6 | `strategy_scorecard(since=...)` / `_india_rows(since=...)` isolate India trades at/after the later of the data epoch and `since`; crypto/commodities and the no-arg call unaffected | ✓ VERIFIED | `strategy_performance.py:163,171,297` (`cutoff = max((e for e in (epoch, since) if e), default=None)`); `server.py` not edited (confirmed via `git show --stat` on both Task 2 commits) — dashboard's no-argument call path unchanged |
| 7 | Dashboard shows both buy-switch states after a restart (`Confirm bars`, `Buy: skip against CPR`, `Buy: skip into OI wall`) | ✓ VERIFIED | `dashboard/src/components/StrategyTuningPanel.tsx:29-31` |
| 8 | Frozen/out-of-scope files never touched: `strategy_learning.py`, `cpr_regime.py`, `sell_strategy.py`, `strategy_mode.py`, `server.py`, anything under `index_ai/brain/`, `executor.py` | ✓ VERIFIED | `git show --stat --format=""` run on all 9 phase commits (`afcb3cc, 35a73ab, b5854a0, f02c7ad, dd5cf54, d1d024f, 81717c5, 5adbc97, ce8151b`) — none lists any of these paths |
| 9 | Richard's Hold decision is recorded, with reasoning, before any `.env` change; the switch reaches the paper lane only through Richard's own hand-edit (which did not happen) | ✓ VERIFIED | `01-04-SUMMARY.md` — decision "Hold" recorded verbatim with reasoning; `git status --porcelain -- index_ai/ tests/ dashboard/src/` empty (independently re-run by this verifier, confirms clean); code defaults for all three levers still off/2 |

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `index_ai/strategies/strategy_params.py` | `buy_block_contra_cpr`, `buy_block_into_oi_wall` fields + `.env` loaders + tuning-summary keys, defaults False | ✓ VERIFIED | Both fields present, `_bool(..., False)` loaders present, `strategy_tuning_summary()` and `env_keys` entries present |
| `index_ai/strategies/buy_strategy.py` | `cpr_contra_filter` + `oi_wall_room_filter` gates, symmetric, buy-lane only | ✓ VERIFIED | 2 occurrences each, bull/bear mirrored |
| `index_ai/strategy_lab.py` | Replay adapter, on-demand candidates, CLI, `LIVE_BUY_TUNED` full bundle | ✓ VERIFIED | `LIVE_BUY_TUNED == {'buy_block_contra_cpr': True, 'buy_block_into_oi_wall': True, 'entry_confirmation_bars': 3}` confirmed live via `python -c` |
| `index_ai/strategies/options_cpr/viability.py` | Buy rows removed, `UNMEASURED` | ✓ VERIFIED | Confirmed live |
| `index_ai/strategy_performance.py` | `strategy_scorecard(since=None)` | ✓ VERIFIED | Signature present, tests pass |
| `dashboard/src/components/StrategyTuningPanel.tsx` | On/Off rows for both switches | ✓ VERIFIED | Present; `npm run build` / `tsc --noEmit` reported clean per 01-03-SUMMARY (not independently re-run by this verifier — low risk, static UI text addition) |
| `tests/test_buy_strategy.py`, `tests/test_strategy_lab.py`, `tests/test_options_cpr.py`, `tests/test_strategy_performance.py` | Coverage for every behavior above | ✓ VERIFIED | 61/61 pass in the targeted run; full suite 683/683 pass |
| `.planning/phases/01-strategy-fixes/01-04-SUMMARY.md` | Decision record, baseline, bar, measuring recipe | ✓ VERIFIED | All elements present; baseline command independently re-runnable (not re-run by this verifier since it requires live journal state identical to SUMMARY's — the mechanism itself, `strategy_scorecard`, is already verified above) |

### Key Link Verification

| From | To | Via | Status |
|---|---|---|---|
| `strategy_lab.py` | `buy_strategy.py` | `evaluate_buy_signal(frame, prev, regime, params=..., oi=...)` | ✓ WIRED (`strategy_lab.py:458`) |
| `strategy_lab.py` | `candle_cache.py` | `candle_cache.load_cached_range(...)` | ✓ WIRED (`strategy_lab.py:345`) |
| `strategy_lab.py` | `options_oi.py` | `analyze_option_chain(...)` | ✓ WIRED (`strategy_lab.py:454`) |
| `buy_strategy.py` | `strategy_params.py` | `cfg.buy_block_contra_cpr`, `cfg.buy_block_into_oi_wall` | ✓ WIRED |
| `daily_ops.py` | `viability.py` | `viability_report()` (unedited consumer, now reads UNMEASURED for buy) | ✓ WIRED |
| `dashboard StrategyTuningPanel.tsx` | `strategy_params.py` | `strategy_tuning_summary()` keys served over the API | ✓ WIRED (row keys match dataclass field names) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Real-chain replay actually runs against real recorded data (not fabricated numbers) | `python -m index_ai.strategy_lab NIFTY` | Output identical to 01-01-SUMMARY.md's recorded numbers (8/1895.19 baseline, 6/233.80 tuned) | ✓ PASS |
| Targeted test suite for this phase's files | `pytest tests/test_buy_strategy.py tests/test_strategy_lab.py tests/test_options_cpr.py tests/test_strategy_performance.py tests/test_strategy_learning.py -q` | 61 passed | ✓ PASS |
| Full project test suite (run once) | `pytest -q` | 683 passed, 0 failed, 182.35s | ✓ PASS |
| Lint on all files this phase touched under `index_ai/` | `ruff check index_ai/strategy_lab.py index_ai/strategies/buy_strategy.py index_ai/strategies/strategy_params.py index_ai/strategies/options_cpr/viability.py index_ai/strategy_performance.py` | "All checks passed!" | ✓ PASS |
| No frozen/live-money file touched by any of this phase's commits | `git show --stat --format="" <commit>` on all 9 commits | No match for `strategy_learning.py`, `cpr_regime.py`, `sell_strategy.py`, `strategy_mode.py`, `server.py`, `executor.py`, `index_ai/brain/` | ✓ PASS |
| No `.env`/live-arming change from this phase | `git status --porcelain -- index_ai/ tests/ dashboard/src/` | Empty | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| STRAT-01 | 01-01, 01-02, 01-03, 01-04 | Buy-lane entries reworked to raise real win rate toward 65% | **Pending — by design** | Gates built, tested, replayed on real data, and off by default. Richard reviewed the real-chain sanity check and chose Hold; win rate has not moved because nothing was switched on. `REQUIREMENTS.md` traceability correctly shows this unchecked. This is the phase's one open item, tracked as a human-decision item, not a code gap. |
| STRAT-02 | 01-01, 01-02, 01-04 | Every parameter change goes through the confidence ladder | ✓ SATISFIED | Ladder module untouched; every new lever ships off by default; the one human-scale change (a paper switch-on) went through an explicit blocking decision checkpoint, and Richard chose not to proceed. `REQUIREMENTS.md` correctly shows this checked. |
| STRAT-03 | 01-02, 01-03 | Strategy Lab verdicts re-validated / pre-cutover numbers flagged | ✓ SATISFIED | Stale Black-Scholes buy-lane numbers deleted from `viability.py`; Strategy Lab docstring states all verdicts are chain-data-only from 2026-09-23 forward. `REQUIREMENTS.md` correctly shows this checked. |

No orphaned requirements — `REQUIREMENTS.md`'s Phase 1 rows (STRAT-01/02/03) match exactly the union of `requirements:` fields across all four plans.

### Anti-Patterns Found

None. Grepped every file this phase modified under `index_ai/` and `dashboard/src/` for `TBD|FIXME|XXX|TODO|HACK|PLACEHOLDER` and stub-empty-return patterns — no matches.

### Minor Note (not a gap)

Plan 01-04 Task 1's acceptance criteria required "The India Paper-mode precondition was confirmed before asking" to be recorded in the SUMMARY. The 01-04-SUMMARY.md references the precondition ("Per this plan's own precondition on Task 1...") but does not explicitly state the dashboard was checked and read Paper before Richard was asked. Since the outcome was Hold (nothing switched on, no live-money exposure either way), this is a documentation completeness note rather than a safety issue — flagging it for the record, not as a blocking gap.

### Human Verification Required

### 1. Whether to revisit the buy-lane tightening trial

**Test:** Decide whether to run a switch-on trial of `BUY_BLOCK_CONTRA_CPR` (and optionally `BUY_BLOCK_INTO_OI_WALL`) now that all the plumbing, tests, and a real-chain sanity check exist — or to keep holding.
**Expected:** A recorded decision (this phase already has one: Hold, 2026-09-30). If revisited and switched on, `strategy_scorecard(since=FLIP)` per the recipe in `01-04-SUMMARY.md` becomes the path to close STRAT-01.
**Why human:** This is a real-money-adjacent trading-risk judgment call the phase's own process (confidence ladder + blocking checkpoint) was deliberately built to route to Richard, not to a verifier or an automated check. The evidence (real-chain replay showing NIFTY/BANKNIFTY worse on both counts) is already in front of him; whether that's still the right call, or whether to test the CPR-only lever in isolation next (01-03's own suggestion), is his call to make going forward.

### Gaps Summary

No code-level or process-level gaps found. Every artifact, switch, gate, test, retirement of stale numbers, and wiring link the four plans committed to actually exists, is correctly wired, defaults safely off, and is covered by passing tests (61 targeted + 683 full-suite). The phase's only open item — the buy lane's win rate has not yet moved — is the documented, correct result of Richard's own Hold decision on thin (6-9 trade) real-chain evidence, not a shortfall in what was built. This phase is functionally complete and safe; STRAT-01 stays open pending a future human decision to run the trial (or a follow-up phase/plan), which is why overall status is `human_needed` rather than `passed` or `gaps_found`.

---

_Verified: 2026-09-30T12:52:38Z_
_Verifier: Claude (gsd-verifier)_
