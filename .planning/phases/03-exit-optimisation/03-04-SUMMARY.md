---
phase: 03-exit-optimisation
plan: 04
subsystem: api
tags: [exit-recheck, replay, market-log, read-only, confidence-ladder, trailing-stop]

requires:
  - phase: 03-exit-optimisation
    provides: "03-01 exit_recheck (_india_rows, _segment_row, _verdict, run_recheck, segment ids india_<INDEX>_<lane>)"
  - phase: 01-strategy-scorecard
    provides: "strategy_learning ladder (_state/_frozen/OBSERVE_MAX/READY_MIN_DAYS)"
provides:
  - "exit_recheck.replay_segment / replay_diagnostic / _market_log_ro / _candidates / _replay_exit / _quotes_at / _net_at and the constants MATCH_MIN, MATCH_TOLERANCE_S, QUOTE_MAX_AGE_S, CANDIDATE_FACTORS, DISTANCE_STEP"
  - "India suggestion gate in _verdict: verdicts suggestion, no_better_distance, replay_unreliable (suggestion dict on the segment row)"
  - "strategy_lab._index_path(instrument, session, db=None)"
affects: [03-05 crypto/commodities/drift/Telegram/daily run, 03-06 dashboard panel]

actuals:
  tokens: 9200
  tasks: 3
  commits: 2

tech-stack:
  added: []
  patterns:
    - "Market log opened only through a mode=ro URI (its own connect helper writes: WAL, migration, commit)"
    - "Replay-vs-replay comparison on the same trades, so pricing error cancels between today's distance and a candidate"
    - "Fixed gate order: ladder -> frozen -> replay quality -> net rupees after real charges"

key-files:
  created: []
  modified:
    - index_ai/exit_recheck.py
    - index_ai/strategy_lab.py
    - tests/test_exit_recheck.py

key-decisions:
  - "Thresholds kept exactly as planned (MATCH_MIN 0.8, MATCH_TOLERANCE_S 120, QUOTE_MAX_AGE_S 300); the real-data run did not tune them"
  - "Candidate rounding: halves round up (floor(x/5 + 0.5)), so BANKNIFTY buy 55 tries 40, 70, 85"
  - "A suggestion needs ALL 40 of the newest 40 trades priced at every distance (plan: compared < OBSERVE_MAX -> replay_unreliable)"
  - "The rough-figure sentence is shown for any lane when the winning distance held a trade past its real exit"

requirements-completed: []  # EXIT-01 spans plans 03-01/04/05/06; the orchestrator marks it when the phase verifies

duration: ~40min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 04: India replay and suggestion gate Summary

**A read-only replay of past India trades on the recorded index ticks and option-chain bid/ask (real Dhan charges, never a proxy), plus a suggestion gate that only ever writes a sentence and numbers into the stored re-check result; on today's real data the replay reproduces 32 of 37 real exits but can price only 5 trades, so every segment would honestly say "replay not reliable yet".**

## Accomplishments

- `_market_log_ro()` opens `memory/market_log.sqlite` only through `?mode=ro` (a test proves CREATE TABLE, DELETE and CREATE INDEX all raise); the market log's own connect helper is never used by this module.
- `_replay_exit` walks the recorded ticks (exchange time) with the same 1:1 anchor rule as `strategy_lab._trail_hit`, stops at the 15:10 square-off, keeps a non-stop real exit (manual, signal, square-off) at its real time unless the replayed stop fires first.
- `_quotes_at` / `_net_at` price each leg from the newest recorded chain snapshot at or before the exit, at most 300 s old: bought leg closes at the bid, sold leg at the ask, journal entry prices, `leg_charge_rupees` both sides (BSE for SENSEX). A leg with no quote is "could not price".
- `replay_segment` returns trades_with_ticks, matched, match_rate, could_not_replay, could_not_price, compared and unrounded net / wins / extended per distance; `replay_diagnostic()` runs all six segments for a manual look and is never called by the daily run.
- `_verdict` India branch: not ready -> `not_enough_data`; frozen -> `working` (both before the replay is opened); then `replay_unreliable` (unreadable log, match rate < 0.8, fewer than 40 priced), `no_better_distance`, or `suggestion` with a plain sentence ending "Nothing was changed - this is only a suggestion for you to approve."
- `strategy_lab._index_path` gained the optional `db=None` keyword; nothing else in that file changed and `tests/test_strategy_lab.py` is untouched and green.

## Task Commits

1. **Task 1 (replay, TDD)** - `fe9e040` feat. RED was observed (tests failed with AttributeError before the code existed) but tests and code went in one commit.
2. **Task 2 (real-data feasibility run)** - no commit: the real data revealed no replay bug (see below), so no code change was allowed or needed.
3. **Task 3 (gate, TDD)** - `56c5995` feat. RED observed (10 failing) before the code, one commit.

## Task 2: real-data feasibility (informational - not a suggestion, no segment is ready)

Run on a `sqlite3.backup` copy of `memory/trade_memory.sqlite` (read from a `mode=ro` connection); the market log was opened by the module itself with `mode=ro`; `run_recheck` was never called. The live server (started 2026-10-02 18:41) was writing the market log the whole time and was not disturbed. **Run time: 13.9 s** for all six segments (7 GB log, per-session tick cache); the first attempt failed only because a scratch file of mine named `copy.py` shadowed the standard library, not because of the product.

| Segment (today's stop) | Replayed | With ticks | Matched | Match rate | Could not replay | Could not price | Priced at every distance |
|---|---|---|---|---|---|---|---|
| NIFTY buy (25) | 3 | 3 | 3 | 1.00 | 0 | 2 | 1 |
| NIFTY sell (40) | 10 | 10 | 8 | 0.80 | 0 | 10 | 0 |
| BANKNIFTY buy (55) | 4 | 4 | 4 | 1.00 | 0 | 3 | 1 |
| BANKNIFTY sell (100) | 10 | 10 | 8 | 0.80 | 0 | 7 | 3 |
| SENSEX buy (80) | 3 | 1 | 1 | 1.00 | 2 | 1 | 0 |
| SENSEX sell (130) | 9 | 9 | 8 | 0.89 | 0 | 9 | 0 |
| **All** | 39 | 37 | 32 | 0.86 | 2 | 32 | 5 |

Net rupees after real charges, over the trades priced at every distance (informational only):

| Segment | Priced | Today's distance | Candidates |
|---|---|---|---|
| NIFTY buy | 1 | 25: -289.4 | 20, 30, 40: all -289.4 (stop never fired earlier or later than the real exit) |
| BANKNIFTY buy | 1 | 55: -661.9 | 40, 70, 85: all -661.9 |
| BANKNIFTY sell | 3 | 100: -1,012.1 | 75: -529.8 / 125: -1,389.2 (1 held longer) / 150: -1,525.8 (3 held longer) |
| NIFTY sell, SENSEX buy, SENSEX sell | 0 | no trade could be priced at every distance | - |

The 2 "could not replay" are SENSEX buys with no `closed_at` (Pitfall 6).

**Why so few priced (real data, not a bug).** The recorded chain lists only strikes near the spot at each moment, and most snapshots do not contain both legs of a spread (hedge legs sit further out). For the 37 trades with a close time, the newest snapshot at the real exit contained all legs for only 9. A read-only side check (not shipped) found the legs' own newest quotes were usually seconds to a few minutes old: a per-leg lookback of up to 300 s would price 33 of the 37 (4 legs were never quoted before the exit). That is a different pricing rule from the one the plan specifies (whole snapshot), so it is a decision for Richard / a later plan, not something this task changed; the thresholds were not touched.

**Plain reading.** The replay's timing is good: on the real trades it reproduced about 86% of what really happened (every segment at or above the 0.8 bar, though the two 0.80 sell segments sit exactly on it with only 10 trades). What it cannot do yet is put a rupee figure on most trades, because the recorded option prices usually do not include every leg at the moment the trade closed; only 5 of 37 trades could be priced at every distance. Today no segment is near the 40-trade, 15-day bar anyway, but when one gets there the gate would say "replay not reliable yet" unless the price recording improves or the pricing rule is relaxed. That is the plan's accepted fallback, not a failure. Nothing here should be read as a stop-distance recommendation.

## Deviations from Plan

None - plan executed exactly as written. Notes that are not deviations:
- `replay_segment` takes an optional `_paths` cache argument (shared across segments in `replay_diagnostic`) and `compute_segments` now gets today's distance from a new `_distance()` helper; both internal.
- `_verdict` now takes `(row, trades)`; 03-01's `test_verdict_ladder_ready` was updated as the plan requires (a ready, not-frozen India segment goes through the replay gate; with no price log it says `replay_unreliable`).
- Plan line `ruff ... versus git stash` replaced by before/after runs, per the orchestrator's rules.

## Hand-applied reviewer checklists (no subagent tool; the orchestrator also runs the real agents)

**trading-safety-reviewer**
1. Blocking I/O on the loop - clean. No new handler; the replay is sync code reached only from `compute_segments`, which the 03-01 handlers already run in `asyncio.to_thread`. Info: it reads a 7 GB log (13.9 s for six segments when every segment is replayed); today the daily run replays nothing because no segment is ready.
2. Read-modify-write - clean. No new shared state or lock (lock count 13, unchanged); `replay_segment` keeps its caches in local variables.
3. Arming interlock - untouched. `grep update_env_values|set_feature_flag|write_text` finds nothing in the module; `SELL_TRAIL_POINTS` and `get_instrument` are only read; `test_a_suggestion_changes_nothing` proves the sell and buy distances and the throwaway `.env` are unchanged after a run that produced a suggestion.
4. Cost model - uses `charges.leg_charge_rupees` (flat Rs20/order brokerage, BSE for SENSEX) and the recorded bid/ask; no hardcoded half-spread. Info: entry prices are the journal's own, exit prices recorded bid/ask; the same treatment applies to today's distance and every candidate, so the offset cancels in the comparison.
5. Order sequencing - not applicable (no order path).
6. Market log - read only via `mode=ro`; `market_log.connect()` not used (grep empty); a test proves writes raise. Observation: the live server writes the log (its -wal/-shm files come and go); a read-only reader coexists with it.

**strategy-tuning-reviewer**
1. Ladder intact - the gate acts only on `state == "ready"` from the imported `_state` (trades AND days); spy tests prove the replay is never opened at 39 trades/20 days or 40 trades/14 days.
2. Frozen first - `row["frozen"]` is checked at `_verdict` before `_india_replay_gate` is called (grep: frozen at line 210, `replay_segment(` at 227); a spy that raises proves a net-positive ready segment is never replayed.
3. Suggest vs apply - the suggestion is a sentence plus a dict in the stored row; no code path from it to `SELL_TRAIL_POINTS`, instruments or `.env` (test above).
4. No backtest data as proof - only recorded ticks and recorded chain bid/ask from the live scanner; no candles, no Black-Scholes, no interpolation; unpriceable legs are counted and reported. A replay is still a counterfactual, which is why it is gated by the match rate and carries the "rough" label.
5. Parity - crypto and commodities untouched (`no_replay_data`, 03-05).
Findings for the reviewer (judgement, not blocking):
- (a) The 40-priced-trades rule means all of the newest 40 trades must price at every distance; with today's quote coverage a suggestion is effectively unreachable until recording or the pricing rule changes (see Task 2).
- (b) "Beats today's net" is strict `>` with no margin, so Rs1 better over 40 trades would produce a suggestion (and a sentence saying "Rs0 more" if it rounds to zero). A minimum margin was not in the plan; worth deciding before the gate can ever fire.
- (c) The rough label is shown for sells too when the wider stop held a trade longer (Pitfall 5 says sells are cleaner); this errs on the side of caution.

**test-isolation-reviewer**
1. Nothing reached calls `index_ai.notify`; no Telegram variable is set; `tests/conftest.py` untouched.
2. No broker or HTTP path.
3. Replay tests use a tmp market log (conftest points `market_log.DB_PATH` at tmp_path; verified in conftest.py:60) filled with fixed 2026-10-01 synthetic ticks and chain rows. Gate tests patch the journal feed (03-01 `feed`), `_market_log_ro` and `replay_segment`; `test_gate_missing_price_log...` runs the real code against a tmp path that does not exist (read-only open fails, creates nothing). No test touches the real `memory/` files or the real `.env`; no test depends on the weekday or the current time (fixed dates only).
4. Info: the `mlog` fixture writes the tmp log through `market_log.connect()`, which is safe only because conftest redirects `DB_PATH`; it would write the real file if that autouse fixture were ever removed.

## Verification

- `python -m pytest tests/test_exit_recheck.py tests/test_strategy_learning.py tests/test_strategy_lab.py -q` - 61 passed (exit_recheck file: 30 tests; strategy_lab untouched, green).
- `-k "india_replay or index_path_db_param or market_log_ro"` - 7 passed. `-k "suggest or gate or verdict"` - 16 passed, including 0.79/0.80 and 39/40 boundaries.
- Acceptance greps: `mode=ro` present; `market_log.connect` absent; `def _index_path(instrument: str, session: str, db: ...` present; `Nothing was changed` present; frozen before `replay_segment(` in `_verdict`.
- Task 2 command ended with `ok` (six segment results).
- Lock tripwire: 13 (unchanged). Ruff: 7 errors before and after (same seven pre-existing); none in `exit_recheck.py`, `strategy_lab.py` or the test file.

## Issues Encountered

None in the product. Open question for Richard / a later plan: relax pricing to each leg's newest quote within 300 s (prices 33 of 37 real trades instead of 9) or improve chain recording so both legs are present at every snapshot.

## Known Stubs

None. `suggestion` is `None` unless the gate passes; crypto/commodity rows still say `no_replay_data` by design (03-05).

## Threat Flags

None. The plan's T-03-13 (mode=ro only, write test), T-03-14 (gate order, rough label, no proxy), T-03-15 (no path to a parameter, unchanged-values test) and T-03-16 (replay only for ready, not-frozen segments, per-session cache, worker thread) are mitigated as planned.

## Self-Check: PASSED

- Commits `fe9e040` and `56c5995` exist; `index_ai/exit_recheck.py`, `index_ai/strategy_lab.py`, `tests/test_exit_recheck.py` and this file are present.
- Task 1 and Task 3 acceptance criteria met; Task 2 acceptance criteria met (six results, `ok`, table and plain reading above, no code diff for the task).

---
*Phase: 03-exit-optimisation*
*Completed: 2026-10-03*
