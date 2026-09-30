---
phase: 01-strategy-fixes
plan: 04
subsystem: strategy
tags: [options-buy-lane, confidence-ladder, decision-record, strategy-lab]

# Dependency graph
requires:
  - phase: 01-strategy-fixes
    provides: "01-03's real-chain baseline-vs-tuned sanity check and Hold recommendation (BUY_BLOCK_CONTRA_CPR, BUY_BLOCK_INTO_OI_WALL, ENTRY_CONFIRMATION_BARS=3)"
provides:
  - "Richard's recorded decision (Hold) on switching the tightened buy-lane entry rules on for paper trading"
  - "A fresh candlestick_buy baseline (NIFTY/BANKNIFTY/SENSEX, PAPER, since the 2026-09-10 data epoch) for whoever revisits this decision later"
  - "The unchanged 40-trade / 65% win-rate / net-positive bar and the one-line command to check it, ready for reuse whenever the switch is next considered"
affects: []

actuals:
  tokens: 2400
  tasks: 1
  commits: 1

tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified: []

key-decisions:
  - "Richard's decision: Hold. Neither BUY_BLOCK_CONTRA_CPR nor BUY_BLOCK_INTO_OI_WALL goes on. Reason he gave: NIFTY (the index he weighs first) came out worse on both win rate and net rupees in 01-03's real-chain sanity check, BANKNIFTY also got worse, only SENSEX improved -- and the two filters were tested together so which one (if either) helped SENSEX can't be isolated from that run."
  - "Task 2 (the .env edit + restart) is skipped entirely per the plan's own instruction on a Hold answer -- there is nothing to switch on, so no .env change and no server restart happened."
  - "No repo source file was touched by this plan -- it is a decision-and-record plan only, matching its files_modified: [] frontmatter."

requirements-completed: [STRAT-02]

coverage:
  - id: D1
    description: "Richard's Hold decision recorded verbatim, with his reasoning, in this SUMMARY (Task 1)"
    requirement: STRAT-02
    verification:
      - kind: other
        ref: "This SUMMARY's Decisions Made / key-decisions section"
        status: pass
    human_judgment: false
  - id: D2
    description: "Fresh three-index candlestick_buy PAPER baseline recorded via strategy_scorecard(since=None), plus the exact measuring command and the 40-trade/65%/net-positive bar for a future re-check (Task 3, adapted for Hold)"
    verification:
      - kind: other
        ref: "python -c \"...strategy_scorecard...\" (command and output recorded below)"
        status: pass
    human_judgment: false
  - id: D3
    description: "No tracked source file changed by this plan"
    verification:
      - kind: other
        ref: "git status --porcelain -- index_ai/ tests/ dashboard/src/"
        status: pass
    human_judgment: false

duration: ~10min
completed: 2026-09-30
status: complete
---

# Phase 1 Plan 4: Buy-Lane Tightening Decision — Hold Summary

**Richard reviewed plan 01-03's real-chain sanity check and chose to hold: the tightened buy-lane entry rules (skip against CPR direction, skip into a nearby OI wall) stay off in paper trading, because NIFTY and BANKNIFTY both came out worse on win rate and net rupees over the ~1 week of real prices tested.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-09-30T18:05:00+05:30 (est.)
- **Completed:** 2026-09-30T18:15:00+05:30 (est.)
- **Tasks:** 1 (of the plan's 3 -- Tasks 1 and 2 resolved without further action, see below)
- **Files modified:** 1 (this SUMMARY)

## Accomplishments

- Recorded Richard's decision on plan 01-03's checkpoint: **Hold** — don't switch `BUY_BLOCK_CONTRA_CPR` or `BUY_BLOCK_INTO_OI_WALL` on for paper trading.
- Recorded his reasoning in his own terms: NIFTY (the index he looks at first) got worse on both counts (win rate and rupees) with the tuned bundle; BankNifty also got worse; only Sensex improved, and because the two new filters were tested together in that run there's no way to tell which one (if either) is responsible for Sensex's improvement.
- Task 2 (the `.env` edit and server restart) did not happen — the plan itself skips this task entirely on a Hold answer, and Richard was not asked to touch `.env` or restart anything, since nothing is being turned on.
- Re-ran the three-index baseline measurement (see below) as of today, for whoever picks this decision back up later.

## How this plan's checkpoint was resolved

The orchestrator already showed Richard plan 01-03's real-chain sanity-check numbers and the three options (full bundle / CPR-rule only / hold) directly in chat, ahead of this plan running. Richard's answer, given in that conversation: **"Hold — don't switch on yet."**

This matches plan 01-03's own recommendation exactly ("Recommendation for plan 01-04: Hold... this week's sample went the wrong way on the index that matters most (NIFTY)"). Per this plan's own precondition on Task 1 (India trading mode must read Paper), and per STRAT-02's standing rule that the confidence ladder decides — never time pressure — holding on a result this thin (6-9 trades per index over about a week) is exactly what following the ladder looks like: observe, don't apply until there is real evidence.

Nothing in `index_ai/`, `dashboard/src/`, or `tests/` needed to change for this decision — that is why this plan's frontmatter declares `files_modified: []`.

## Fresh baseline (recorded today, for later reference)

Same measuring recipe plan 01-03 and this plan both use — `candlestick_buy` PAPER trades, all rows summed per index, since the 2026-09-10 data epoch:

```
python -c "import sys; from index_ai.strategy_performance import strategy_scorecard as s; since=(sys.argv[1:] or [None])[0]; rows=s(since=since)['india']['rows']; [print(i, n, w, round(100*w/n,1) if n else None, round(t,2)) for i in ('NIFTY','BANKNIFTY','SENSEX') for r in [[x for x in rows if x['instrument']==i and x['mode']=='PAPER' and x['strategy'].startswith('candlestick_buy')]] for n,w,t in [(sum(x['trades'] for x in r), sum(x['wins'] for x in r), sum(x['net'] for x in r))]]"
```

Run 2026-09-30, output (instrument, trades, wins, win rate %, net rupees):

| Instrument | Trades | Wins | Win rate | Net (Rs) |
|---|---|---|---|---|
| NIFTY | 14 | 3 | 21.4% | -6,034.30 |
| BANKNIFTY | 9 | 2 | 22.2% | -7,000.50 |
| SENSEX | 4 | 2 | 50.0% | +1,297.84 |

(This is today's live-journal read under the *current, untightened* rules — a few more trades have accrued since plan 01-03's own read on the same morning, which is expected; it is not the tuned-bundle number, since the tuned bundle never went live in this plan.)

## The bar, unchanged, for whenever this is revisited

Per D-06/D-07 (plan 01-04's own frontmatter) and STRAT-02: a switch-on trial counts as **fixed** only once, counting paper trades from the switch-on time forward, NIFTY reaches **trades >= 40 AND wins/trades >= 0.65 AND summed net rupees > 0**. At 39 trades no verdict is given yet. 26 wins of 40 (65.0%) passes; 25 of 40 (62.5%) does not. BANKNIFTY and SENSEX are then checked against the same bar, NIFTY judged first.

**To measure it later** (once a switch-on time `FLIP` — an IST ISO timestamp — exists), the exact same command with `FLIP` as its one argument:

```
python -c "import sys; from index_ai.strategy_performance import strategy_scorecard as s; since=(sys.argv[1:] or [None])[0]; rows=s(since=since)['india']['rows']; [print(i, n, w, round(100*w/n,1) if n else None, round(t,2)) for i in ('NIFTY','BANKNIFTY','SENSEX') for r in [[x for x in rows if x['instrument']==i and x['mode']=='PAPER' and x['strategy'].startswith('candlestick_buy')]] for n,w,t in [(sum(x['trades'] for x in r), sum(x['wins'] for x in r), sum(x['net'] for x in r))]]" "2026-XX-XXTHH:MM:SS+05:30"
```

**Confidence-ladder note:** the tuner never applies anything by itself. Nothing in this plan is a real-money step — this trial, whenever it runs, is paper only. Arming real money for the buy lane would be a wholly separate, explicit decision by Richard after the bar above is met, same as every other lane in this project.

## Recommended next step (forward-looking, not executed here)

Per 01-03's own recommendation, carried forward unchanged: the two switches (`BUY_BLOCK_CONTRA_CPR`, `BUY_BLOCK_INTO_OI_WALL`) were tested together in the one real-chain run available, so which one (if either) is doing the work on SENSEX can't be told apart from that run. A future attempt, if Richard wants to move forward, would test the CPR-direction rule alone against a fresh sample before either switch reaches paper trading — that isolates the one lever with the strongest journal-backed reasoning (the losing NIFTY buys went against the CPR direction or came on sideways days, per 01-01/01-03) from the newer, less-tested OI-wall rule.

## To switch it on later (unchanged from 01-03/01-04's original instructions, kept here for reference)

Richard edits `.env` by hand (Claude cannot — permission-blocked) and restarts the server:
- Full bundle: `BUY_BLOCK_CONTRA_CPR=true`, `BUY_BLOCK_INTO_OI_WALL=true`, `ENTRY_CONFIRMATION_BARS=3`
- CPR rule only: `BUY_BLOCK_CONTRA_CPR=true`
- To switch off again: delete those lines (or set to `false`), save, restart.

Both switches still default to `false` and `ENTRY_CONFIRMATION_BARS` still defaults to `2` in code — unchanged by this plan, per `index_ai/strategies/strategy_params.py`.

## Task Commits

This plan makes no source changes; only the SUMMARY and planning-state docs are committed.

**Plan metadata:** (this commit) `docs(01-04): record Hold decision on buy-lane tightening`

## Files Created/Modified

- `.planning/phases/01-strategy-fixes/01-04-SUMMARY.md` — this file

## Decisions Made

- **Richard: Hold.** Neither `BUY_BLOCK_CONTRA_CPR` nor `BUY_BLOCK_INTO_OI_WALL` goes on. NIFTY and BANKNIFTY both got worse on win rate and net rupees in 01-03's sanity check; SENSEX improved but the two levers weren't isolated from each other in that run, so the SENSEX result can't be attributed to either one specifically.
- No `.env` change, no server restart — Task 2 is explicitly skipped on a Hold answer per the plan text.
- STRAT-02 (confidence ladder honored, no shortcut) is marked complete by this plan: the plan demonstrates the ladder was actually followed in practice, holding on a ~1-week/6-9-trade sample rather than switching on for paper. STRAT-01 (buy lane's real win rate moved toward 65%) is **left Pending** — the win rate has not moved, because nothing was switched on; that remains open for a future phase/plan once Richard chooses to run the trial.

## Deviations from Plan

None — plan executed exactly as written, adapted per this plan's own stated conditional: Task 1's decision was Hold, so Task 2 was skipped entirely (as instructed), and Task 3 recorded the choice, the baseline, and the plan 01-03 red-flag reasoning instead of a switch-on time (also as instructed, since Task 3's `<action>` explicitly covers the "On hold" branch).

## Issues Encountered

None.

## User Setup Required

None — no `.env` change or server restart was needed for a Hold decision.

## Next Phase Readiness

- Phase 1 (Strategy Fixes) has now run all 4 of its plans. STRAT-03 is complete (01-02/01-03). STRAT-02 is complete as of this plan. **STRAT-01 remains open** — the buy lane's measured win rate has not moved from baseline, because the tightened rules were held rather than switched on. Whoever next works this phase (or a follow-on phase) should read this SUMMARY's baseline and bar before re-raising the switch-on question, rather than re-deriving it.
- No blockers introduced. The buy lane keeps running today's untightened rules; no live-arming, no code change, no risk-path touched.
- If Richard wants to revisit this later: test `BUY_BLOCK_CONTRA_CPR` alone first (per 01-03's own recommendation), against a fresh sample, before bundling in `BUY_BLOCK_INTO_OI_WALL`.

## Self-Check: PASSED

- This SUMMARY confirmed present on disk (`[ -f ]`).
- Baseline command re-run today, output matches the table above (NIFTY 14/3/21.4%/-6034.30, BANKNIFTY 9/2/22.2%/-7000.50, SENSEX 4/2/50.0%/+1297.84) — 3 output lines, no traceback.
- `git status --porcelain -- index_ai/ tests/ dashboard/src/` confirmed empty before this commit (no source file touched).

---
*Phase: 01-strategy-fixes*
*Completed: 2026-09-30*
