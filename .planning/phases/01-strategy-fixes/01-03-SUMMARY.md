---
phase: 01-strategy-fixes
plan: 03
subsystem: strategy
tags: [options-buy-lane, oi-walls, strategy-lab, backtest-replay, dashboard]

# Dependency graph
requires:
  - phase: 01-strategy-fixes
    provides: "01-01's cpr_contra_filter gate, replay adapter (_prev_day/_live_buy_read), and live_buy_lane/live_buy_lane_tuned Strategy Lab candidates"
provides:
  - "StrategyParams.buy_block_into_oi_wall switch (env BUY_BLOCK_INTO_OI_WALL, default false)"
  - "oi_wall_room_filter NO_TRADE gate in evaluate_buy_signal, buy-lane-only, symmetric bull/bear, WALL_ROOM_PCT = 0.10% of price"
  - "strategy_lab.LIVE_BUY_TUNED full bundle (buy_block_contra_cpr, buy_block_into_oi_wall, entry_confirmation_bars=3)"
  - "Dashboard Strategy tuning panel rows for both buy switches"
  - "Three-index real-chain baseline-vs-tuned sanity check with a plain-English read and a recommendation"
affects: [01-04]

actuals:
  tokens: 4211
  tasks: 3
  commits: 3

tech-stack:
  added: []
  patterns: ["second buy-lane-only gate mirrored symmetrically in both direction branches, same shape as cpr_contra_filter/st_filter", "gate ordering: supertrend veto -> cpr_contra_filter -> oi_wall_room_filter -> confidence tweak -> action"]

key-files:
  created: []
  modified:
    - index_ai/strategies/strategy_params.py
    - index_ai/strategies/buy_strategy.py
    - index_ai/strategy_lab.py
    - tests/test_buy_strategy.py
    - dashboard/src/components/StrategyTuningPanel.tsx
    - guides/Strategy Guide.md

key-decisions:
  - "WALL_ROOM_PCT = 0.10 (percent of price) chosen because it lines up with the buy-lane's own 1:1 trail distance on every index — 0.10% of a ~24,000 NIFTY is ~24 points vs. the live 25-point trail; 0.10% of ~55,000 BANKNIFTY is ~55 vs. the live 55-point trail; 0.10% of ~81,000 SENSEX is ~81 vs. the live 80-point trail. A wall closer than that caps the trade at a loss or a scratch before the trail can lock anything in."
  - "The gate sits after cpr_contra_filter and before the confidence tweak in both branches — same ordering pattern as the existing st_filter/cpr_contra_filter gates, so a future third buy-lane gate has an obvious place to go."
  - "LIVE_BUY_TUNED now carries all three levers (buy_block_contra_cpr, buy_block_into_oi_wall, entry_confirmation_bars=3); the live ENTRY_CONFIRMATION_BARS code default stays 2 — only the lab's tuned replay uses 3, per D-05."
  - "Task 1's strategy-tuning-reviewer pass was done manually against the subagent's written checklist (no subagent-dispatch tool available in this run, same limitation 01-01 hit) — see Issues Encountered."

requirements-completed: [STRAT-01, STRAT-03]

coverage:
  - id: D1
    description: "buy_block_into_oi_wall switch added to StrategyParams (dataclass field, .env loader, tuning summary, env_keys), default off"
    requirement: STRAT-01
    verification:
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_unset_ignores_nearby_wall"
        status: pass
      - kind: other
        ref: "grep -n '_bool(\"BUY_BLOCK_INTO_OI_WALL\", False)' index_ai/strategies/strategy_params.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "oi_wall_room_filter NO_TRADE gate added symmetrically to both bull/bear branches of evaluate_buy_signal, WALL_ROOM_PCT = 0.10% of price, off unless switched on, unaffected when oi=None or the wall is already broken"
    requirement: STRAT-01
    verification:
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_blocks_when_call_wall_too_close"
        status: pass
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_allows_call_wall_with_room"
        status: pass
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_allows_when_call_wall_already_broken"
        status: pass
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_unaffected_when_oi_none"
        status: pass
      - kind: unit
        ref: "tests/test_buy_strategy.py#test_wall_room_gate_bear_mirror"
        status: pass
      - kind: other
        ref: "grep -v '^\\s*#' index_ai/strategies/buy_strategy.py | grep -c oi_wall_room_filter (== 2)"
        status: pass
    human_judgment: false
  - id: D3
    description: "strategy_lab.LIVE_BUY_TUNED carries the full tuned bundle and its module docstring states every verdict is recomputed from market_log.chain rows starting 2026-09-23 (STRAT-03)"
    requirement: STRAT-03
    verification:
      - kind: other
        ref: "python -c \"from index_ai import strategy_lab as s; assert s.LIVE_BUY_TUNED == {'buy_block_contra_cpr': True, 'buy_block_into_oi_wall': True, 'entry_confirmation_bars': 3}\""
        status: pass
      - kind: other
        ref: "grep -n 2026-09-23 index_ai/strategy_lab.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Dashboard Strategy tuning panel shows 'Confirm bars' plus On/Off rows for both new buy switches; Strategy Guide's env table documents the three keys"
    verification:
      - kind: automated_ui
        ref: "npm --prefix dashboard run build (exit 0) + npx tsc --noEmit (0 errors)"
        status: pass
      - kind: other
        ref: "grep -c 'buy_block_contra_cpr\\|buy_block_into_oi_wall' dashboard/src/components/StrategyTuningPanel.tsx (== 2)"
        status: pass
    human_judgment: false
  - id: D5
    description: "Real-chain baseline-vs-tuned sanity check on all three indices (python -m index_ai.strategy_lab), with a plain-English read and a recommendation for plan 01-04's decision"
    requirement: STRAT-01
    verification:
      - kind: other
        ref: "python -m index_ai.strategy_lab (output recorded below)"
        status: pass
    human_judgment: true
    rationale: "Whether the tuned bundle's per-index numbers on ~1 week of real chain data are a meaningful signal (vs. noise from a 6-9 trade sample) is a judgment call for a human, not something an automated check can certify — the recommendation itself (hold / switch full bundle / switch CPR rule only) is a decision for plan 01-04, not a mechanical pass/fail."

duration: ~25min
completed: 2026-09-30
status: complete
---

# Phase 1 Plan 3: OI-Wall Room Gate, Full Tuned Bundle, Dashboard Rows + Three-Index Real-Chain Sanity Check Summary

**A second buy-lane-only gate (`BUY_BLOCK_INTO_OI_WALL`, default off) that skips a buy when the opposing OI wall sits closer than one trail distance, folded into `strategy_lab`'s full tuned bundle and sanity-checked against real recorded chain data on NIFTY, BANKNIFTY and SENSEX — NIFTY and BANKNIFTY came out worse on both win rate and net rupees, SENSEX came out ahead on rupees despite a lower win rate, and the sample (6-9 trades per index) is too thin to call either way.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-30T17:26:00+05:30 (est., before first commit)
- **Completed:** 2026-09-30T17:51:00+05:30 (est.)
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Added `buy_block_into_oi_wall` (env `BUY_BLOCK_INTO_OI_WALL`, default `false`) to `StrategyParams` — a new buy-lane-only switch, symmetric in both direction branches of `evaluate_buy_signal`, that returns `NO_TRADE`/`oi_wall_room_filter` when the opposing OI wall (the strike where the opposite option type has the most open interest) sits closer than `WALL_ROOM_PCT = 0.10%` of price — about one buy-trail distance (NIFTY 25 / BANKNIFTY 55 / SENSEX 80 index points). The 1:1 trail cannot lock a profit before the index moves that far, so a nearer wall caps the trade at a loss or a scratch.
- The gate is unaffected when the switch is off (default), when no `oi` is passed, or when price has already broken through the wall — only a genuinely nearby, undefended wall blocks the trade.
- `strategy_lab.LIVE_BUY_TUNED` now carries the complete tuned bundle: `buy_block_contra_cpr: True`, `buy_block_into_oi_wall: True`, `entry_confirmation_bars: 3` (the live code default for confirmation bars stays 2 — only the lab's tuned replay uses 3, per D-05). The module docstring now states every Strategy Lab verdict is recomputed from `market_log.chain` rows starting 2026-09-23, so there is no pre-cutover verdict to trust.
- The dashboard's Strategy tuning panel gained two rows ("Buy: skip against CPR", "Buy: skip into OI wall") right after "Confirm bars", so Richard can see after a `.env` switch-on and restart whether it actually took effect. The Strategy Guide's env table documents `BUY_BLOCK_CONTRA_CPR`, `BUY_BLOCK_INTO_OI_WALL`, and `ENTRY_CONFIRMATION_BARS`.
- Ran `python -m index_ai.strategy_lab` (no arguments) — the live buy lane's own entry rules, replayed on the real recorded option chain for every session with chain data (2026-09-23 through 2026-09-30, 7 sessions), baseline settings vs. the full tuned bundle, on all three indices. Results and a plain-English read below.

## Real three-index chain run (2026-09-30)

`python -m index_ai.strategy_lab`, replaying every recorded session (2026-09-23 through 2026-09-30):

**Baseline (today's live settings, as actually read from this machine's `.env`):** `buy_block_contra_cpr=false`, `buy_block_into_oi_wall=false`, `entry_confirmation_bars=3`. Note: `entry_confirmation_bars` is already `3` on this machine's live `.env`, not the code default of `2` — the tuned bundle's confirmation-bars value therefore matches baseline here, so the difference between the two columns below comes only from the two OI/CPR switches, not the confirmation-bar count. This is a straight readout of what the CLI printed, not a code change.

**Tuned settings:** `buy_block_contra_cpr=true`, `buy_block_into_oi_wall=true`, `entry_confirmation_bars=3`.

| Instrument | Variant | Trades | Trading days | Win rate | Gross (Rs) | Charges (Rs) | Net (Rs) | Rs/trade | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| NIFTY | live_buy_lane (today) | 8 | 5 | 37.5% | 2392.00 | 496.81 | 1895.19 | 236.90 | COLLECTING |
| NIFTY | live_buy_lane_tuned | 6 | 4 | 33.3% | 607.75 | 373.95 | 233.80 | 38.97 | COLLECTING |
| BANKNIFTY | live_buy_lane (today) | 8 | 5 | 50.0% | -766.50 | 562.30 | -1328.80 | -166.10 | COLLECTING |
| BANKNIFTY | live_buy_lane_tuned | 7 | 4 | 28.6% | -2073.00 | 533.71 | -2606.71 | -372.39 | COLLECTING |
| SENSEX | live_buy_lane (today) | 9 | 5 | 55.6% | 1234.00 | 547.62 | 686.38 | 76.26 | COLLECTING |
| SENSEX | live_buy_lane_tuned | 6 | 4 | 50.0% | 2750.00 | 360.85 | 2389.15 | 398.19 | COLLECTING |

All six rows read `COLLECTING` (6-9 trades and 4-5 trading days per row, well under the 30-trade/14-day readiness bar) — none is a verdict yet, just a sanity check.

**Red-flag test (a) zero tuned trades:** none of the three indices — tuned took 6, 7, and 6 trades respectively. No red flag from this test anywhere.

**Red-flag test (b) tuned worse on BOTH win rate and net rupees, judged NIFTY first (D-02):**
- **NIFTY:** win rate 33.3% < 37.5% AND net Rs233.80 < Rs1,895.19 — **both worse. Red flag.**
- **BANKNIFTY:** win rate 28.6% < 50.0% AND net -Rs2,606.71 < -Rs1,328.80 — **both worse (an already-losing week got more expensive). Red flag.**
- **SENSEX:** win rate 50.0% < 55.6% (worse) but net Rs2,389.15 > Rs686.38 (better) — not both worse. **No red flag.**

## Plain read for Richard

I ran the tightened buy rules against about a week of real recorded prices (23-30 September) on all three indices, comparing them side by side with today's rules. On NIFTY — the index you look at first — today's rules made Rs1,895 over 8 trades; the tightened rules made only Rs234 over 6 trades and won less often too (33% vs 38%). That's worse on both counts. BANKNIFTY was already losing money this week even with today's rules (-Rs1,329 over 8 trades), and the tightened rules lost more (-Rs2,607 over 7 trades). SENSEX is the one bright spot: fewer trades (6 vs 9), a slightly lower win rate, but more money in the end (Rs2,389 vs Rs686). Because two of the three indices got worse on both trades-won and rupees, and NIFTY is the one you weigh first, this week's numbers don't support switching the tightened rules on for real. But a week and 6-9 trades per index is nowhere near proof either way — it only shows the code runs correctly against real prices, not whether the change genuinely helps or hurts. The real answer needs 30 of its own paper trades over 14 trading days, same as every other rule in this project.

**Recommendation for plan 01-04: Hold.** Don't switch either lever on for paper yet — this week's sample went the wrong way on the index that matters most (NIFTY), and the two switches were tested together here so there's no way to tell from this run which one is responsible. If plan 01-04 wants to move forward anyway, testing the CPR-direction rule alone against a fresh sample (separately from the OI-wall rule) would at least isolate which lever is doing what before anything reaches paper trading.

## Task Commits

Each task was committed atomically:

1. **Task 1a (RED): failing tests for the OI-wall room gate** - `81717c5` (test)
2. **Task 1b (GREEN): OI-wall room gate + full tuned bundle** - `5adbc97` (feat)
3. **Task 2: dashboard switch rows + guide docs** - `ce8151b` (feat)
4. **Task 3: real-chain sanity check** - captured in this SUMMARY (no code change; see plan `<output>` — this task's deliverable is the SUMMARY itself)

**Plan metadata:** (this commit)

## Files Created/Modified

- `index_ai/strategies/strategy_params.py` - new `buy_block_into_oi_wall` field + `.env` loader + tuning-summary entry + `env_keys` entry
- `index_ai/strategies/buy_strategy.py` - `WALL_ROOM_PCT = 0.10` module constant, `oi_wall_room_filter` gate added symmetrically to the bull and bear branches
- `index_ai/strategy_lab.py` - `LIVE_BUY_TUNED` gained `buy_block_into_oi_wall` and `entry_confirmation_bars`; `live_buy_lane_tuned` description updated; module docstring gained the 2026-09-23 STRAT-03 note
- `tests/test_buy_strategy.py` - 6 new tests: switch-off unaffected, blocks when wall too close, allows with room, allows when wall already broken, unaffected when `oi=None`, bear mirror
- `dashboard/src/components/StrategyTuningPanel.tsx` - two new On/Off rows after "Confirm bars"
- `guides/Strategy Guide.md` - three new env-table rows (`BUY_BLOCK_CONTRA_CPR`, `BUY_BLOCK_INTO_OI_WALL`, `ENTRY_CONFIRMATION_BARS`)

## Decisions Made

- `WALL_ROOM_PCT = 0.10` (percent of price) was sized to match the buy lane's own 1:1 trail distance on every index (see key-decisions above) rather than picked arbitrarily — the same reasoning the plan's `<action>` laid out.
- Kept the gate ordering consistent with the existing pattern: supertrend veto, then `cpr_contra_filter`, then the new `oi_wall_room_filter`, then the confidence tweak — mirrors how `st_filter` and `cpr_contra_filter` already stack, so a future gate has an obvious slot.
- Did not touch `ML_GATE_BUY_MIN` or anything under `index_ai/brain/` (D-04) — confirmed via `git show --stat` on both commits.
- Reported the strategy_lab CLI's baseline numbers exactly as printed, including the observation that `entry_confirmation_bars` was already `3` in this machine's live `.env` rather than the code default of `2` (see the Real three-index chain run section) — did not touch `.env` or paper over the discrepancy.

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria passed; no Rule 1-3 auto-fixes were needed for the new gate or the dashboard/docs work.

## Issues Encountered

- Task 1's `<action>` calls for running the `strategy-tuning-reviewer` subagent on the diff. As in plan 01-01's Task 2, this executor's toolset in this run did not include a subagent-dispatch tool, so the review was performed manually against the subagent's own written checklist (`.claude/agents/strategy-tuning-reviewer.md`): confirmed via `git status`/`git show --stat` that neither `index_ai/strategy_learning.py`, `index_ai/executor.py`, nor anything under `index_ai/brain/` was touched by either Task 1 commit; confirmed both new levers (`buy_block_into_oi_wall`, the `LIVE_BUY_TUNED` bundle) are plain config reads inside `evaluate_buy_signal` and the lab's tuned replay, with no new code path that suggests or auto-applies a parameter change — nothing here interacts with `strategy_learning.py`'s confidence ladder or its `_frozen()` check at all. No findings. This should be treated as a self-review, not a substitute for a real second-pass review if a subagent-dispatch tool becomes available before the switches are ever flipped on in `.env`.
- This machine's live `.env` already has `ENTRY_CONFIRMATION_BARS=3` set (not the code default of 2) — this was discovered from the `strategy_lab` CLI's own baseline readout, not changed by this plan. It means the baseline-vs-tuned comparison above isolates only the two new switches, not the confirmation-bar count, since both columns already run on 3 confirmed closes. Flagging this for whoever reviews plan 01-04's decision, since it wasn't something this plan set out to check.

## User Setup Required

None - no external service configuration required. `BUY_BLOCK_INTO_OI_WALL` defaults to `false`; switching it (or `BUY_BLOCK_CONTRA_CPR`) on in `.env` and restarting the server is explicitly deferred to plan 01-04's decision checkpoint, per this plan's own `<threat_model>` (T-03-01) and the recommendation above.

## Next Phase Readiness

- Both D-01 levers (CPR-direction gate from 01-01, OI-wall room gate from this plan) plus the confirmation-bars tuning exist behind off-by-default switches, replayed together as `live_buy_lane_tuned`, and are visible on the dashboard.
- Plan 01-04 has a real three-index sanity read to work from, with an explicit "Hold" recommendation and the reason (NIFTY got worse on both counts, and the two switches weren't isolated from each other in this run).
- Blocker/concern for the human: this run's baseline `entry_confirmation_bars` was already 3, not the code default of 2 — worth confirming with Richard whether that's an intentional `.env` setting from an earlier session before plan 01-04 treats these numbers as "today's settings vs. the full new bundle."
- The live buy lane, the sell lane, `strategy_learning.py`'s confidence ladder, and `server.py`'s `/api/strategy-lab` endpoint remain untouched — no live-arming, no auto-apply, no dashboard-endpoint cost change (the two `live_buy_lane*` candidates stay `ON_DEMAND`-excluded from the default `run()`).

## Self-Check: PASSED

- All 6 modified files confirmed present on disk (`[ -f ]`).
- All three task commits (`81717c5`, `5adbc97`, `ce8151b`) confirmed present in `git log --oneline --all`.
- All task-level `<acceptance_criteria>` re-verified passing: `oi_wall_room_filter` grep count == 2, `WALL_ROOM_PCT = 0.10` present, both strategy_params.py greps match, `LIVE_BUY_TUNED` python check prints `ok`, `2026-09-23` docstring grep matches, `git show --stat` confirms no `index_ai/brain/`/`executor.py` file touched, `ruff check` on the three Task 1 files prints "All checks passed!"; dashboard `grep -c` == 2, guide `grep -c` == 3, `npm run build` exit 0, `npx tsc --noEmit` 0 errors.
- Plan-level `<verification>`: `pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_lab.py -q` — 45 passed. Full suite `pytest -q` — 683 passed (up from the 677-test baseline after plan 01-02, +6 new tests), 0 failed, 182.76s.

---
*Phase: 01-strategy-fixes*
*Completed: 2026-09-30*
