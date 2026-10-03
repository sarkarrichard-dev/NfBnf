---
phase: 03-exit-optimisation
plan: 03
subsystem: docs
tags: [comments, docs, trailing-stop, strategy-guide, words-only]

requires:
  - phase: 03-exit-optimisation
    provides: "03-02 deleted the old percent-of-option-price trail"
provides:
  - "code comments and docstrings that describe only the 1:1 index trail"
  - "Strategy Guide with one trailing-stop section, values checked against the live constants"
  - "CLAUDE.md, CONCERNS.md, STRUCTURE.md no longer describe the deleted trail as present"
affects: [03-06 dashboard wording]

actuals:
  tokens: 6500
  tasks: 2
  commits: 2

key-files:
  modified:
    - index_ai/planner.py
    - index_ai/instruments.py
    - index_ai/strategies/option_structures.py
    - index_ai/strategies/credit_spread.py
    - tests/test_credit_spread.py
    - CLAUDE.md
    - .planning/codebase/CONCERNS.md
    - .planning/codebase/STRUCTURE.md
    - guides/Strategy Guide.md

key-decisions:
  - "Words only: no code token changed in any money-path file; proven by AST comparison"

requirements-completed: []  # EXIT-02 spans several plans; the orchestrator marks it when the whole phase verifies

duration: ~10min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 03: Comment and documentation refresh Summary

**Every comment, docstring, guide section and codebase-map line that still described the old option-price trail now describes the one trail that runs (the 1:1 index-point trail, buys 25/55/80, sells 40/100/130), with the guide's numbers checked by a script against the live constants and the five Python files proven AST-identical to before.**

## Accomplishments

- Task 1: five code comments/docstrings and one test heading reworded (planner `_pivot_target`, `instruments._buy_scalp_trail`, the `_HEDGE_PREMIUM_BAND` comment, the `evaluate_credit_open_trade` comment, the `tests/test_credit_spread.py` section heading). `_pivot_target` and the `_bn_bear_call` / `_bn_trade` helpers are kept.
- Task 2: Strategy Guide now has one section, "Trailing stop: one rule for both lanes", with an Index | Buy | Sell table; the contradictory 100/200-point table and the "only long premium" sentence are gone; a note in "Credit spread exits" says the index trail replaces the profit-trail/target/stop rows for directional spreads (iron condors keep them). CLAUDE.md's one stale sentence replaced; CONCERNS.md item marked resolved; STRUCTURE.md line removed. The "Stop checks" section is untouched.

## Task Commits

1. Task 1 - `d0f97b4` docs(03-03): reword comments that still described the old percent-of-option-price trail
2. Task 2 - `96a794f` docs(03-03): document the one real trailing stop in the guide, CLAUDE.md and codebase map

## Proof that no behaviour changed (words only)

AST comparison of the version at `6150de5` (HEAD when the plan started) against the working tree, docstrings stripped from module/class/function bodies, `ast.dump` compared:

```
IDENTICAL index_ai/planner.py
IDENTICAL index_ai/instruments.py
IDENTICAL index_ai/strategies/option_structures.py
IDENTICAL index_ai/strategies/credit_spread.py
IDENTICAL tests/test_credit_spread.py
```

Line-by-line review of `git diff -U0`: only comment, docstring and heading text changed. One extra thing appears in the diff and is not mine: the ruff `--fix`/format PostToolUse hook re-wrapped a few already-existing lines in `index_ai/planner.py` (one `_record_next_expiry(...)` call split over three lines) and in `tests/test_credit_spread.py` (a dict literal, a call, two trailing-comment spacings, blank lines). That is formatting only, and the AST comparison above is the proof it changed nothing.

## Acceptance results

- Task 1: scan for `premium[ _-]trail` over the five files prints `[]` then `ok`; `def _pivot_target` (planner.py:30) and `def _bn_bear_call` (test_credit_spread.py:155) still match; `python -m pytest tests/test_credit_spread.py tests/test_trailing.py tests/test_buy_scalp_trail.py tests/test_exit_credit.py -q`: **21 passed**.
- Task 2: the plan's verify command prints `ok` (guide table rows match `SELL_TRAIL_POINTS` and `get_instrument(...).trail_distance_points`; live values read: NIFTY 25/40, BANKNIFTY 55/100, SENSEX 80/130). `git diff --stat -- CLAUDE.md`: `1 file changed, 3 insertions(+), 3 deletions(-)` (three lines, the one sentence). Guide diff hunks all sit before the "Stop checks" heading, which is unchanged (one occurrence).
- Lock tripwire: 13 (unchanged). Ruff: 7 errors in `index_ai/` before and after, 0 in any touched file.
- Full test suite not run (the orchestrator runs it after the wave).

## Deviations from Plan

**1. [Rule 1 - stale docstring] `evaluate_credit_open_trade` docstring said "(not index-point trail)"**
- **Found during:** Task 1 (reading credit_spread.py 340-440)
- **Issue:** the function's own docstring said the opposite of what the code does (it runs the 1:1 index trail for directional spreads). The plan listed only the comment below it.
- **Fix:** reworded the docstring (docstring only; AST identical).
- **Files modified:** index_ai/strategies/credit_spread.py
- **Commit:** d0f97b4

Notes, not deviations: the test heading was reworded as "BANKNIFTY bear call fixtures for the 1:1 index trail test below" because the helpers under it are fixtures for one BANKNIFTY test, not tests of a trail. CONCERNS.md still shows the original issue text under the "(resolved 2026-10, Phase 3)" title, labelled "as originally recorded". The Strategy Guide says the rupee profit trail stays on for buys and iron condors (it is skipped for bull put / bear call spreads while the index trail runs, per `credit_spread.py`).

**Total deviations:** 1 auto-fixed (docstring). **Impact:** none on behaviour.

## Issues Encountered

None.

## Known Stubs

None.

## Threat Flags

None. T-03-11 mitigated (guide numbers checked by script against the live constants); T-03-12 mitigated (AST-identical, trail/credit suites green).

## Next Phase Readiness

Ready for the remaining wave-2 work. The dashboard wording in `dashboard/src/lib/strategies.ts` is left for plan 03-06 as planned.

## Self-Check: PASSED

- All nine modified files exist; commits `d0f97b4` and `96a794f` exist in `git log`.
- Both tasks' acceptance criteria re-run and met (outputs above).
