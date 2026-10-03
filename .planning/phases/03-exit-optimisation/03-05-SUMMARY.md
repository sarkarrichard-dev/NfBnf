---
phase: 03-exit-optimisation
plan: 05
subsystem: api
tags: [exit-recheck, drift, telegram, crypto, commodities, daily-ops, confidence-ladder]

requires:
  - phase: 03-exit-optimisation
    provides: "03-01 exit_recheck (compute_segments, _segment_row, _verdict, run_recheck, state row); 03-04 India replay gate"
provides:
  - "crypto:point_trail and commodities:atr_trail segments, with their own exit vocabularies (classify_exit kinds crypto / commodity)"
  - "rule bookkeeping: state['rules'] per venue, window restart on a changed trail rule"
  - "baselines + drift (state['baselines'], row drift / drift_detail / baseline), _moved_more_than on exact fractions, one Telegram message per drift event"
  - "daily_ops.run_eod step: report['exit_recheck'] = run_recheck('daily')"
affects: [03-06 dashboard panel (new row keys drift, drift_detail, baseline; new segment ids)]

actuals:
  tokens: 10000
  tasks: 3
  commits: 3

tech-stack:
  added: []
  patterns:
    - "Drift compared as fractions.Fraction, never floats; rounding is display-only"
    - "Alert on the stored OK -> DRIFT transition only: state stored first, message after, inside try/except"
    - "Venue rule remembered in the stored state; a changed rule restarts that segment's window at the moment it is first seen"

key-files:
  created: []
  modified:
    - index_ai/exit_recheck.py
    - index_ai/daily_ops.py
    - tests/test_exit_recheck.py

key-decisions:
  - "Pooled crypto segment id is crypto:point_trail and commodities is commodities:atr_trail; the India ids stay india_<INDEX>_<lane>, so the alert key is exit-drift:india_NIFTY_sell:<captured_at> (the plan's example 'exit-drift:india:sell:NIFTY' predates the 03-01 id scheme)"
  - "Per-venue _live_rule(venue) instead of one _live_rules(): a failing venue is recorded in errors without losing the other, and its last-seen rule is kept"
  - "Extended the crypto rules for the 7 real singleton shapes that first landed in other (cloud, RSI+EMA, coin removed, take_profit, stop_loss)"
  - "tests' feed fixture now also points both journal paths at nonexistent tmp files, so every test that runs compute_segments/run_recheck is isolated from the real journals"

requirements-completed: []  # EXIT-01 / EXIT-03 span plans 03-01/04/05/06; the orchestrator marks them when the phase verifies

duration: 10min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 05: Crypto, commodities, drift warning and daily run Summary

**The stop re-check now also covers crypto (one pooled 1.6% point-trail segment) and MCX commodities (one pooled ATR-trail segment), remembers a baseline per segment and sends one plain Telegram message when the stop rate or win rate moves more than 15 points (exact-fraction comparison), and runs by itself every trading day after the close as a contained step of the end-of-day job.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-10-03T09:08:25Z
- **Completed:** 2026-10-03T09:18:43Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- Crypto: closed_at at or after 2026-09-25T12:00Z, `ak_roxx_pro` and `btc_daily_straddle` excluded (own trails), USD net of fees. Commodities: whole journal after the data epoch, INR. Both journal paths are read as module attributes at call time and bad/blank lines are skipped.
- Exit reasons classified per market into the shared classes; with no recorded price path a ready, not-frozen crypto/commodity segment says `no_replay_data` (never a suggestion), a frozen one says `working`.
- The live rule (crypto `point_trail_pct`; the five commodity `ATR_K_*` constants) is stored every run; a different rule restarts that venue's window at the run time and the old baseline is dropped without an alert.
- Baseline `{trail_hits, wins, n, trail_hit_rate, win_rate, distance, captured_at, drifted}` captured the first time a segment is ready under a distance, kept in the `exit_recheck_state` row across restarts; "current" is the newest 40 trades.
- Drift: stop hit rate or win rate more than 15 points from baseline, on `Fraction` ratios. 22/40 vs 16/40 is correctly not drift; 23/40 vs 16/40 is; 1500/10000 vs 0 is not, 1501/10000 is. Below the ladder bar nothing is captured and nothing can drift.
- One Telegram message per drift event, plain words, before/after percentages, "Nothing was changed". Sent after the new state is stored, inside try/except, key `exit-drift:<segment>:<baseline captured_at>`.
- `daily_ops.run_eod()` gets one 7-line try/except step (between strategy_learning and cloud_backup): `report["exit_recheck"] = run_recheck("daily")` or `{"error": ...}`. No new scheduler, loop, thread or lock.

## Task Commits

1. **Task 1: crypto and commodities segments, vocabularies, rule bookkeeping** - `c5a9ce7` (feat; RED observed, tests and code in one commit)
2. **Task 2: baselines, 15-point exact-ratio drift, one plain Telegram message** - `86eec2d` (feat; RED observed: 9 failing before the code)
3. **Task 3: daily run as a run_eod step** - `b41b6d8` (feat; RED observed: 2 failing before the step)

## Real-data exit-class coverage (Task 1 item 7; journals only read, `run_recheck` never called)

`classify_exit` over every `exit_reason` in the live journals, after the rule extension:

| Journal | Rows | trail_stop | manual | time_exit | signal_exit | other_rule | other |
|---|---|---|---|---|---|---|---|
| crypto (all rows) | 248 | 28 | 82 | 23 | 64 | 51 | 0 |
| crypto pooled segment (since 2026-09-25T12:00Z, minus ak_roxx_pro / btc_daily_straddle) | 49 (8 trading days, net -254.54 USD) | 28 | 11 | 3 | 7 | 0 | 0 |
| commodities (all rows = pooled segment) | 27 (4 trading days, net +1,780.10 INR) | 18 | 5 | 3 | 1 | 0 | 0 |

Reasons classed `other`: **none** (empty set for both journals). Before the extension seven real singleton rows landed in `other`, and the rules were extended for exactly these: `Close # back into cloud (top #).` x2 and `RSI + EMA rolled over with ADX confirming - momentum reversed` x1 -> signal_exit; `coin removed` x2 -> manual (an operator action); `take_profit` / `stop_loss` (BTC straddle only, excluded from the pooled segment) -> other_rule. All are in `test_exit_classifier`.

Neither pooled segment is anywhere near the ladder bar (40 trades AND 15 trading days), so **no real segment can drift or alert today** - "no drift alerts in the first weeks" is correct, not broken.

## Deviations from Plan

None - plan executed exactly as written. Notes that are not deviations:
- `_live_rule(venue)` replaces the plan's single `_live_rules()` (per-venue failure containment, see key-decisions); `_rules_since(prev_rules, venue, live)` takes the venue.
- The existing `test_segments` / `test_endpoints` asserted six segments; they now assert eight (six India + the two pooled), the plan's intended change.
- `_verdict` text for crypto/commodity changed to the plan's wording; the India gate from 03-04 is untouched.
- `feed` fixture gained the two journal-path patches (see key-decisions) so the older tests cannot read the real crypto/commodity journals.
- The existing `tests/test_daily_ops.py::test_eod_runs_once_per_day` now also executes `run_recheck("daily")` against its conftest tmp DB, reading the real crypto/commodity journals read-only - the same exposure the strategy_learning step already has. A fresh tmp DB has no baseline, so no alert is possible there. The test was not edited and is green.

## Hand-applied reviewer checklists (no subagent tool; the orchestrator also runs the real agents)

**trading-safety-reviewer**
1. Blocking I/O in async handlers - clean. No handler added; `run_recheck` stays reachable only from the 03-01 handlers (already `asyncio.to_thread`) and from `run_eod` (already thread-offloaded by `scanner._run_eod_if_due` / `server._eod_catch_up`). `notify.alert` hands the send to its own daemon thread.
2. Read-modify-write - clean. Baselines are read and rewritten under the existing `_RECHECK_LOCK`, whole state stored as absolute values with the upsert (T-03-19). Lock tripwire 13, unchanged.
3. Arming / money path - untouched. `grep update_env_values|set_feature_flag|write_text` in `exit_recheck.py` finds nothing; no stop value, env key or strategy parameter is written; nothing under `crypto/` or `commodities/` changed (`git diff --stat` per task: only the plan's files). The alert says "Nothing was changed".
4. Cost model - crypto `pnl_usd` and commodity `net_rupees` are already net of fees; no charge maths added.
5. Outbound message - T-03-17: stored `drifted` flag is the primary de-dup (notify's stamp file forgets after 3 days), notify key the second guard. Observation: `notify.alert` has a default 1-hour window, so a second alert for the same key (drifted -> recovered -> drifted within an hour) is suppressed by notify; the stored flag still shows it.
6. Info: with a very small `n` (the bar is 40) the drift rule is deliberately blunt; at 40 trades one trade moves a rate 2.5 points, so 15 points is 6+ trades.

**strategy-tuning-reviewer**
1. Ladder intact - baselines and drift only for `state == "ready"` (imported `_state`: trades AND days). `test_drift_below_bar` proves a 100-point swing at 39 trades neither captures a baseline nor alerts, and that an existing baseline is kept, not judged, when a segment falls below the bar.
2. Frozen first - the 03-04 `_verdict` order is unchanged; crypto/commodity ready+frozen -> `working` before `no_replay_data`.
3. Suggest vs apply - crypto and commodities never get a suggestion; no backtest data, candles or Black-Scholes proxy anywhere in the new code (they get stats and drift only). A drift is a warning, not a recommendation.
4. Rule changes - a changed live rule restarts the window and drops the baseline, so numbers from two different stops are never mixed. Finding (judgement): the restart happens when the re-check first sees the new rule, so up to one day of trades under the old rule can be counted (flagged assumption in the plan, accepted).
5. Finding (judgement): pooling hides a coin- or contract-specific problem inside one segment (RESEARCH A3); per-coin views stay in the existing scorecard.
6. Info: the crypto "since" for a first run is the research-verified 2026-09-25T12:00Z; if the live rule was already something other than 1.6 at the very first stored run, that first window mixes rules once.

**test-isolation-reviewer**
1. No real Telegram - every drift test replaces `index_ai.notify.alert` with a capture list (or a raiser); `grep -rn "TELEGRAM_BOT_TOKEN\|TELEGRAM_CHAT_ID" tests/test_exit_recheck.py` prints nothing; `tests/conftest.py` untouched.
2. No real journals - the `journals` fixture and (new) the `feed` fixture point `crypto.journal.JOURNAL_PATH` and `commodities.lanes.JOURNAL_PATH` at tmp files; the DB is the conftest tmp DB; the live crypto rule is patched on `crypto.config.crypto_settings`. Info: the two `run_eod` hook tests patch `run_recheck`, so they read nothing real; only the untouched `test_eod_runs_once_per_day` reads the real journals read-only (accepted by the orchestrator, no alert possible).
3. Deterministic - fixed 2026-11-xx dates; `now_ist_iso` and `_utc_now_iso` are monkeypatched; no test depends on the weekday or the time.
4. No broker or HTTP path in any new test.

## Verification

- `python -m pytest tests/test_exit_recheck.py tests/test_daily_ops.py tests/test_notify.py tests/test_strategy_performance.py tests/test_strategy_learning.py tests/test_reconcile_fault_injection.py -q` - 88 passed (exit_recheck file: 47 tests).
- Acceptance: `-k "classifier or crypto or commodit or rule_change"` 5 passed; `-k drift` 9 passed (>= 6, includes exact-15-point and alert-once); `-k run_eod` 2 passed; `Fraction` inside `_moved_more_than`; `exit-drift:` inside a try/except; TELEGRAM grep empty; `run_recheck("daily")` sits between the strategy_learning and cloud_backup steps; `git diff --stat -- index_ai/daily_ops.py` = 7 insertions only.
- Lock tripwire 13 (unchanged). Ruff: 7 errors before and after (the same seven pre-existing); none in `exit_recheck.py`, `daily_ops.py` or the test file.
- Full suite not run (the orchestrator runs it after the wave).

## Issues Encountered

None in the product.

## Known Stubs

None. The crypto/commodity `no_replay_data` verdict is the intended honest answer (no recorded price path), not a stub.

## Threat Flags

None. T-03-17..T-03-22 mitigated as planned (one alert per stored transition, tests capture the alert, one lock around compute-and-store, tmp journals, text built only from segment numbers and labels, EOD step contained).

## Self-Check: PASSED

- Commits `c5a9ce7`, `86eec2d`, `b41b6d8` exist; `index_ai/exit_recheck.py`, `index_ai/daily_ops.py`, `tests/test_exit_recheck.py` and this file are present.
- All three tasks' acceptance criteria met.

---
*Phase: 03-exit-optimisation*
*Completed: 2026-10-03*
