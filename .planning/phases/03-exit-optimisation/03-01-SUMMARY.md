---
phase: 03-exit-optimisation
plan: 01
subsystem: api
tags: [exit-recheck, trailing-stop, confidence-ladder, learned_settings, fastapi, sqlite]

requires:
  - phase: 01-strategy-scorecard
    provides: strategy_learning ladder (_state/_frozen), strategy_performance net-of-charges helpers
provides:
  - "index_ai/exit_recheck.py: classify_exit, compute_segments, run_recheck, last_result, learned_settings row exit_recheck_state"
  - "GET /api/exit-recheck and POST /api/exit-recheck/run"
  - "Shared segment-row shape (segment, venue, lane, instrument, label, distance, distance_label, currency, trades, trading_days, state, frozen, window_n, wins, trail_hits, win_rate, trail_hit_rate, net, exit_mix, verdict, message, suggestion, drift, baseline) that later plans and the dashboard read"
affects: [03-04 India replay, 03-05 crypto/commodities/drift/Telegram/daily run, 03-06 dashboard panel]

actuals:
  tokens: 5200
  tasks: 2
  commits: 2

tech-stack:
  added: []
  patterns:
    - "Ladder imported from strategy_learning, never copied"
    - "Single learned_settings row, whole state written as absolute values under a module lock"
    - "Busy caller waits for the running pass and returns the stored result"

key-files:
  created:
    - index_ai/exit_recheck.py
    - tests/test_exit_recheck.py
  modified:
    - index_ai/server.py

key-decisions:
  - "Ladder bar is strategy_learning's 40 trades AND 15 trading days (imported). D-03 says 14 days; its intent is 'the same bar used everywhere else', so 15 is used. One-line change if Richard wants exactly 14."
  - "Segment = (lane, index), not the scorecard's (strategy_mode, index); paper and live pooled"
  - "Segment id format is india_<INDEX>_<lane> (e.g. india_NIFTY_sell) - the stable key the dashboard and baselines will use"
  - "POST is not behind require_admin_secret (cannot move money or change a stop); whole /api is behind DASHBOARD_PASSWORD when set"

patterns-established:
  - "Exit-reason classes: trail_stop, time_exit, manual, signal_exit, other_rule, legacy_premium, other (unknown is counted, never dropped)"

requirements-completed: []  # EXIT-01, EXIT-03 span several plans; the orchestrator marks them when the whole phase verifies

coverage:
  - id: D1
    description: "Re-check recomputes all six India segments (3 indexes x buy/sell) under today's stop distance and reports trades, days, win rate after charges, stop-hit share, net"
    requirement: "EXIT-01"
    verification:
      - kind: unit
        ref: "tests/test_exit_recheck.py#test_segments"
        status: pass
    human_judgment: false
  - id: D2
    description: "Every India exit reason lands in a named class; unseen reasons count as other"
    requirement: "EXIT-01"
    verification:
      - kind: unit
        ref: "tests/test_exit_recheck.py#test_exit_classifier"
        status: pass
      - kind: other
        ref: "read-only run on a consistent copy of the real journal: 90 real notes, 0 classed other"
        status: pass
    human_judgment: false
  - id: D3
    description: "Verdict follows the confidence ladder one step either side of both thresholds; frozen is checked first; below the bar the real numbers are still returned"
    requirement: "EXIT-01"
    verification:
      - kind: unit
        ref: "tests/test_exit_recheck.py#test_verdict_ladder_not_enough"
        status: pass
      - kind: unit
        ref: "tests/test_exit_recheck.py#test_verdict_ladder_ready"
        status: pass
    human_judgment: false
  - id: D4
    description: "Result is stored in one learned_settings row, served by GET, survives a restart, and POST/GET never change a stop or the .env"
    requirement: "EXIT-01"
    verification:
      - kind: integration
        ref: "tests/test_exit_recheck.py#test_endpoints"
        status: pass
    human_judgment: false
  - id: D5
    description: "Overlapping runs never interleave; a busy caller waits and returns the stored result without recomputing"
    requirement: "EXIT-03"
    verification:
      - kind: unit
        ref: "tests/test_exit_recheck.py#test_run_recheck_waits_when_busy"
        status: pass
    human_judgment: false
  - id: D6
    description: "On the real journal every India segment honestly says 'not enough data yet' with its real numbers"
    requirement: "EXIT-01"
    verification:
      - kind: other
        ref: "Task 2 read-only command, ends with ok (tables below)"
        status: pass
    human_judgment: false

duration: 9min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 01: Exit re-check tracer Summary

**"Re-check now" end to end for the six India stop segments: API call -> journal -> exit-reason classifier -> stats under today's stop distance -> confidence-ladder verdict -> one restart-safe stored row; first real output is an honest "not enough data yet" for all six.**

## Performance

- **Duration:** ~9 min
- **Completed:** 2026-10-03T08:16Z
- **Tasks:** 2 (Task 1 tracer, Task 2 read-only real-data run)
- **Files modified:** 3 (2 created, 1 edited)

## Accomplishments

- `index_ai/exit_recheck.py`: classifier (ordered rules, first match wins), per-(lane, index) segments filtered by each trade's own recorded stop distance, ladder verdict imported from `strategy_learning`, one `learned_settings` row (`exit_recheck_state`), module `_RECHECK_LOCK`, `run_recheck` / `last_result`.
- `GET /api/exit-recheck` and `POST /api/exit-recheck/run`, both `async def` + `await asyncio.to_thread`, lazy import, POST takes no body.
- 9 tests on synthetic rows (classifier, segments, 39/40 trades and 14/15 day boundaries, frozen-first, endpoints + restart + stops/.env unchanged, lock).
- Tracer gate: `<verify>` re-run after the tracer commit, passed (46 tests), so expansion is allowed. "Tracer verified end-to-end - expanding."

## Task Commits

1. **Task 1 (tracer, TDD)** - RED `cfd5d00` (test), GREEN `0749919` (feat). No refactor commit needed.
2. **Task 2 (real-data check)** - no commit: the real journal produced zero unclassified notes, so the only code change Task 2 permitted (classifier additions) was not needed; the command is a read-only check, nothing to commit.

**Plan metadata:** docs commit follows this file.

## Task 2: real-data results (read-only copy of memory/trade_memory.sqlite)

The real file was opened only with a `?mode=ro` URI and copied with the sqlite3 backup API into a temp folder; `compute_segments()` (not `run_recheck`) ran against the copy. Nothing was written to the real file.

| Segment | Trades | Trading days | Win rate (net) | Stop-hit share | Net rupees | Verdict |
|---|---|---|---|---|---|---|
| NIFTY buy (25-pt) | 3 | 3 | 33.3% | 33.3% | -1,205.40 | not_enough_data |
| NIFTY sell (40-pt) | 10 | 5 | 30.0% | 90.0% | -1,706.48 | not_enough_data |
| BANKNIFTY buy (55-pt) | 4 | 2 | 25.0% | 50.0% | -2,033.59 | not_enough_data |
| BANKNIFTY sell (100-pt) | 10 | 4 | 30.0% | 90.0% | -5.17 | not_enough_data |
| SENSEX buy (80-pt) | 3 | 3 | 33.3% | 0.0% | -1,225.22 | not_enough_data |
| SENSEX sell (130-pt) | 9 | 4 | 22.2% | 88.9% | -1,253.54 | not_enough_data |

Counts match RESEARCH (buy 3/4/3, sell 10/10/9). All six report "not enough data yet" with the real numbers shown, which is the correct first output. (The NIFTY sell net differs in sign from the +1,016 in CLAUDE.md: that figure is over a different 9-trade set and date range; this table is only trades taken under today's 40-point distance. Not investigated further - out of scope, and 10 trades is far below the bar.)

All 90 real India exit notes (every closed trade, any distance):

| Class | Count |
|---|---|
| trail_stop | 31 |
| legacy_premium | 24 |
| signal_exit | 15 |
| time_exit | 13 |
| manual | 6 |
| other_rule | 1 |
| other | 0 |

Notes classed `other`: none. No classifier change or test addition was needed.

## Files Created/Modified

- `index_ai/exit_recheck.py` - the re-check module (new)
- `tests/test_exit_recheck.py` - 9 tests on synthetic rows (new)
- `index_ai/server.py` - the two routes, placed right after `/api/strategy-learning`

## Decisions Made

- 15 trading days (the ladder) rather than D-03's 14; recorded in the module docstring path via the import and here.
- Segment id `india_<INDEX>_<lane>`; rows carry the shared shape later plans extend (baseline/drift/suggestion fields exist now, set to `None`/`False`).
- `wins`, `trail_hits` and both rates are over the newest 40 trades (window); `trades`, `trading_days` and `net` are over all trades under today's distance. This is what the plan specified; the dashboard should label it.

## Deviations from Plan

None - plan executed exactly as written. Two small notes, neither a deviation: the acceptance check `ruff check` on the touched files is clean, and Task 2 ended without a commit because it needed no code change.

## Hand-applied reviewer checklists (no subagent tool; the orchestrator also runs the real agents)

**trading-safety-reviewer**
1. Blocking I/O on the event loop - clean. Both handlers (`server.py:1604`, `:1615`) are `async def` + `await asyncio.to_thread`; all SQLite work is in the sync module.
2. Read-modify-write races - clean. The module writes the whole state as absolute values; `_RECHECK_LOCK` covers recompute-and-store; the POST takes no delta or body. Finding (info only): a waiting caller reads the stored row after the other run releases the lock, so it may return a result labelled `trigger: "daily"` to a button press - correct and intended.
3. Live-arming interlock - untouched. grep for `update_env_values|set_feature_flag|write_text|atomic_write` in the module prints nothing; `test_endpoints` proves the throwaway .env stays empty and `SELL_TRAIL_POINTS` and each `trail_distance_points` are unchanged after a POST.
4. Cost model - uses `_india_charges` (real Dhan charges + measured spread) unchanged. Finding (inherited, same as the scorecard): a trade with no priced legs falls back to gross rather than net. Left as-is for consistency with `strategy_performance`; relevant only if old unpriced rows ever fall under today's distance.
5. Order sequencing - not applicable (no order path touched).
6. Accepted: the POST is not behind `require_admin_secret` (T-03-03). No rate limit, but the lock means repeated clicks do not queue extra recomputes.

**strategy-tuning-reviewer**
1. Ladder intact - `_state`, `OBSERVE_MAX`, `READY_MIN_DAYS`, `FREEZE_LOOKBACK` imported (grep confirmed; no local 40/15). Gate is trades AND days; tests cover 39/20, 40/14, 40/15.
2. Frozen means untouchable - `_verdict` checks state first, then `frozen`, before anything else on the ready path; suggestion is always `None` in this plan.
3. Suggest vs apply - nothing here writes a parameter; only the module's own row.
4. No backtest data as proof - live journal only; no replay in this plan.
5. India/crypto parity - India only now; crypto and commodities join in 03-05 on the same ladder. Findings (info): `_frozen` here sees net-after-charges pnls, while `strategy_learning` feeds it gross - plan-specified and stricter; and D-03's 14 vs the ladder's 15 days is a deliberate, recorded one-day-stricter choice.

**test-isolation-reviewer**
1. Nothing reached by the tests calls `index_ai.notify`; the module has no notify call. Telegram env vars are not set anywhere in the file; conftest's clearing is untouched.
2. No broker or HTTP path reached.
3. `recent_trades`, `day_review._exit_notes` and `data_epoch` are monkeypatched; conftest redirects the SQLite DB, `.env` and market log to `tmp_path`. `TestClient(app)` is used without `with`, so no scanner/lifespan starts. The only write is the `learned_settings` row in the tmp DB.
4. `tests/conftest.py` not modified. Info: the lock test uses a 0.3 s sleep to prove the thread is blocked - low flake risk, the assertion only needs the thread to still be alive.

## Verification

- `python -m pytest tests/test_exit_recheck.py tests/test_strategy_learning.py tests/test_strategy_performance.py tests/test_day_review.py tests/test_admin_auth.py -q` - 46 passed.
- `-k "classifier or segments or verdict_ladder or endpoints or busy"` - 8 passed, 1 deselected (9 tests in the file, all pass).
- Task 2 real-data command ended with `ok`.
- Lock tripwire: `grep -rn "threading.Lock()\|threading.RLock()" index_ai crypto --include=*.py | wc -l` = 13 (was 12; the one added is `_RECHECK_LOCK`).
- Ruff: 7 errors before and after (same seven pre-existing ones); zero in `exit_recheck.py`, `server.py` or the test file.

## Issues Encountered

None.

## Known Stubs

None. `suggestion` is always `None` and `drift`/`baseline` are `False`/`None` by design in this tracer (03-04 and 03-05 fill them); the verdict for a ready, not-frozen segment says plainly that a different stop cannot be tested yet.

## Threat Flags

None - the two new routes are the ones in the plan's threat model (T-03-01 to T-03-03 mitigated or accepted as planned).

## Next Phase Readiness

Ready for 03-02 (premium trail removal) and the later expansions (03-04 replay, 03-05 other venues + drift + daily run, 03-06 dashboard). Nothing blocks them.

## Self-Check: PASSED

- `index_ai/exit_recheck.py`, `tests/test_exit_recheck.py`, this file: present on disk.
- Commits `cfd5d00` (test) and `0749919` (feat) exist in `git log`.
- Task 1 acceptance criteria all met (tests, imports, `exit_recheck_state`, `ON CONFLICT(key)`, `to_thread` routes without `require_admin_secret`, forbidden-writer grep empty, ruff clean, checklists listed above). Task 2 acceptance criteria met (six segments all not_enough_data, tables above, no code diff).

---
*Phase: 03-exit-optimisation*
*Completed: 2026-10-03*
