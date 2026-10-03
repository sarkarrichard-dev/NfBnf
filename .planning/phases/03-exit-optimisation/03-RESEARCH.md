# Phase 3: Exit Optimisation - Research

**Researched:** 2026-10-03
**Domain:** Dead-code removal on the stop/trail money path + a read-only, suggest-only re-check/drift monitor over the live journals (India SQLite, crypto JSONL, commodities JSONL). Pure Python/FastAPI/React work. No new libraries.
**Confidence:** HIGH (every in-repo claim below was read or queried this session; the two places where data does not exist are stated plainly instead of papered over)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** The re-check and drift warning cover **every segment** — India options (NIFTY/BANKNIFTY/SENSEX, buy and sell lanes), crypto (the 1.6% `point_trail_pct` trail in `crypto/strategies/trailing.py`) and MCX commodities. Richard chose "Everything" over the roadmap's India-only wording. Segments with thin data simply report "not enough data yet" (see D-03) rather than being excluded. — Reversibility: reversible (scope of a read-only report).
- **D-02:** When fresh trades say a stop distance looks wrong, the system **suggests a better number for Richard to approve** (plain-language, in rupees and win-rate points: "NIFTY buy 25-pt stop looks too tight — 30 would have kept ₹X more over the last N trades"). It never changes a stop by itself. Same confidence-ladder posture as Phase 1 and the standing ML guardrails: suggest only, human approves, never auto-apply.
- **D-03:** A segment needs **40+ trades and 14+ trading days** before the system may say a stop looks wrong or suggest a change; below that it says "not enough data yet". Same bar used everywhere else in the project.
- **D-04:** Delete `index_ai/premium_trail.py` fully, including its tests (`tests/test_premium_trail.py`) and every import/branch that references it (`index_ai/trailing.py`, `index_ai/position_exits.py`, comment in `entry_guard.py`). **First copy its useful history** (the 2026-09-23 "5% first target, 25% almost never armed, replay of 37 real MTM paths" findings and the per-index config reasoning) into `memory/strategy-findings.md` so the lesson survives the deletion. **Before deleting, confirm whether any live trade can still reach it** — initial scouting shows `trailing.py` only runs it for non-long-premium positions on NIFTY/BANKNIFTY/SENSEX, and credit spreads run their own index trail in `credit_spread.py`, so it looks unreachable, but research must prove that rather than assume it. — Reversibility: reversible via git history.
- **D-05:** The point-based index trail (stop follows the index 1:1) **counts as both the trailing stop and the trailing profit** for Richard's standing "every segment needs both" rule. Do not add a second profit mechanism. The rupee `profit_trail` fallback used when no option quote exists stays as it is — this phase does not remove it.
- **D-06:** "Drifting" means a stop's hit-rate or win-rate moves **more than 15 percentage points** from its last-check baseline, and only once the segment has 40+ trades (D-03). Avoids alarms from a couple of unlucky trades.
- **D-07:** A drift warning goes out as a **Telegram message plus a visible flag on the dashboard** (Strategy P&L tab). One message per drift event, not repeated every scan (reuse the existing de-duplicating `notify.alert` key pattern).
- **D-08:** The re-check runs **automatically once a day after the market closes, plus a dashboard button to run it any time.** Crypto has no market close, so its daily run uses the same end-of-day time as India — exact time is Claude's discretion.

### Claude's Discretion
- Exact end-of-day run time (D-08), and where the baseline ("last check") numbers are stored, as long as they survive a server restart.
- Exact wording and placement of the dashboard flag and the "Re-check now" button, reusing existing Strategy P&L tab patterns and shared UI primitives.
- How exit reasons are read back out of the journal for the hit-rate numbers (initial scouting found exit reasons are not under the obvious `exit_reason` keys in `option_json`; research must find where they actually live and whether every segment records them consistently).

### Deferred Ideas (OUT OF SCOPE)
- Automatically applying a suggested stop distance — explicitly ruled out; only ever a human-approved change.
- Removing the rupee `profit_trail` fallback — kept as-is by D-05, not this phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXIT-01 | Trailing-stop point values (NIFTY/BANKNIFTY/SENSEX, buy and sell lanes) are re-validated against recent live-journal data on a recurring basis instead of staying a one-time hardcoded tune | Q2 (exit reasons live in `feedback.note`), Q3 (what can/cannot be replayed; today every India segment has 3-10 trades under the current distance, so the honest first output is "not enough data yet" with the numbers still shown), Q4 (reuse `strategy_learning._state`/`_frozen`), Q5 (hook into `daily_ops.run_eod`) |
| EXIT-02 | The dead `premium_trail.py` percent-of-premium code path is removed so there is exactly one trailing-stop implementation | Q1: proven unreachable for every lane x index; full deletion surface with file:line; the ONE behaviour that silently depends on it (`position_exits._premium_trailed_credit`) and the exact replacement |
| EXIT-03 | A monitoring signal exists if trail-stop hit rate or win rate drifts meaningfully from its last validated baseline | Q2 (hit-rate definition and per-segment exit classifiers), Q5 (baseline persistence + alert), Q6 (dashboard flag) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

Extracted from `./CLAUDE.md` (read this session). The planner must verify compliance:

- Windows/PowerShell: never hand the user `KEY=value` bash syntax. `.env` is permission-blocked — do not read or write it; a new setting must be a dashboard/API action, never "edit .env". Requiring a customer to hand-edit `.env` is not acceptable for any feature.
- **Money path — extra care:** `index_ai/trailing.py`, `index_ai/strategies/credit_spread.py` (`SELL_TRAIL_POINTS`), `index_ai/scanner.py` (tick-driven stop triggering) are named. The `trading-safety-reviewer` agent applies to the `trailing.py` + `position_exits.py` edits.
- **Never put blocking I/O in an `async def` handler** — use `asyncio.to_thread` (both new endpoints and any daily hook).
- Read-modify-write on a shared file/setting needs a lock; the client sends absolute values, not deltas (applies to the baseline store).
- Dashboard: any `dashboard/src` change needs `npm --prefix dashboard run build` (and `npx tsc --noEmit`); the `ui-consistency-reviewer` agent applies.
- Tests must not leak real side effects: `tests/conftest.py` autouse clears `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; never re-set them (only `tests/test_notify.py` does). The `test-isolation-reviewer` agent applies to any new test that can reach `notify` or write state.
- Checks before commit: `python -m pytest -q` (660 tests, ~7-8 min), `ruff check index_ai/` (~8 pre-existing cosmetic errors; compare against `git stash`).
- Trailing stops are **index-point-based** (buys NIFTY 25 / BANKNIFTY 55 / SENSEX 80; sells NIFTY 40 / BANKNIFTY 100 / SENSEX 130; crypto separate point trail). `premium_trail.py` is "bypassed" per CLAUDE.md — verified below to be truly unreachable, not merely bypassed.
- `strategy-tuning-reviewer` (agent file read this session) rules apply to the suggestion code: ladder gate on trades AND days, **frozen (net-positive) is checked before any suggestion**, suggest != apply, no historical-backtest data as ground truth.

## Summary

**EXIT-02 is a safe, small deletion with exactly one trap.** `premium_trail.py` is unreachable for every live and paper trade that can exist today. The buy lane is excluded by `not _is_long_premium(action, tx)` at `index_ai/trailing.py:244`; the sell lane (all three indices) returns early into `evaluate_credit_open_trade` at `trailing.py:190-196` before the premium branch at line 235 is ever reached. The only theoretical remainder is a naked single-leg `SELL_ATM_PUT/CALL` position, and nothing in the signal engine emits those actions (verified by grep; also `APEX_USE_HEDGED_SPREADS` defaults True at `strategy_params.py:24`). The journal confirms it empirically: the last of 46 trades whose `trail_meta` carries a `pt_entry` key was created 2026-09-24T13:47; none since. **The trap:** `position_exits._premium_trailed_credit()` (`position_exits.py:32-37`) is NOT dead — it returns True for every directional credit spread on NIFTY/BANKNIFTY/SENSEX, and `strategy_exit_reason` (`position_exits.py:96`) uses it to suppress the signal-flip / CPR-regime / EMA-cross exits for the whole sell lane. Deleting the import without a replacement would make every live sell start closing on regime flips. The replacement must be `inst.upper() in SELL_TRAIL_POINTS` (the real owner of those exits), which preserves behaviour exactly, including the existing FINNIFTY-closes-on-flip test.

**EXIT-01/03 can be built almost entirely from existing parts, but the data is thin and one important thing cannot be replayed.** Exit reasons are not in `option_json`; they live in the `feedback` table as `note = "{reason} @ {IST time}"` (`exit.py:390`, `learning.py:1419-1422`), and `index_ai/day_review.py` already has `_exit_notes()` and `_bucket_exit()` to read and bucket them (reuse, with two gaps: it has no "manual close" bucket and crypto's own `_bucket_exit` does not recognise the live `point trail:` reason). Crypto and commodities carry `exit_reason` directly on JSONL rows, in three different vocabularies, so a small per-segment classifier into a common set (trail stop / time exit / manual / signal exit / other) is required. The confidence ladder (`strategy_learning._state`, `_frozen`, `OBSERVE_MAX=40`) is reusable as-is, but note it requires **15** trading days, not the 14 in D-03 (flagged below). A "would a different distance have done better" replay is feasible **for India only**: real index ticks exist for every session since 2026-08-29 and real option-chain quotes since 2026-09-23, and `strategy_lab` already has `_index_path`/`_trail_hit`/`_fill`/`leg_charge_rupees` to reuse. Crypto and commodities have **no recorded intra-trade price path anywhere**, so their honest answer to D-02 is "no replay data yet" (drift monitoring still works for them; it needs only exit reasons and P&L).

**Right now every segment will say "not enough data yet".** Closed trades under today's exact stop distances: India buy NIFTY 3 / BANKNIFTY 4 / SENSEX 3; sell NIFTY 10 / BANKNIFTY 10 / SENSEX 9 (2-5 trading days each); crypto point-trail-eligible 49 trades over at most 8 days; commodities 27 trades over 4 days (last 2026-09-17). Drift can never fire today; it must be proven with synthetic fixtures in tests.

**Primary recommendation:** One new module `index_ai/exit_recheck.py` (segment builder + exit classifier + ladder gate + baseline/drift + optional India replay), hooked into `daily_ops.run_eod()` as one more try/except step, exposed by `GET /api/exit-recheck` + `POST /api/exit-recheck/run` (both `await asyncio.to_thread(...)`), baseline stored as a `learned_settings` row (gets conftest DB isolation and S3 backup for free), alert via `notify.alert(..., key=...)` on state *transition* only, and one `ExitRecheckPanel` in `StrategyPerformancePage.tsx`. Do the deletion (EXIT-02) first and independently.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Live stop/trail evaluation (unchanged this phase) | API / Backend (`trailing.py`, `credit_spread.py`, `scanner.py`) | — | Stop logic is server-side, tick/20s driven; this phase only deletes a dead branch |
| Exit-reason classification + segment stats | API / Backend (`exit_recheck.py`) | — | Reads SQLite/JSONL journals; pure compute |
| Alternative-distance replay (India) | API / Backend (reuses `strategy_lab` helpers) | Database (`market_log.sqlite` ticks + chain) | Needs recorded index ticks and chain quotes |
| Baseline persistence | Database / Storage (`learned_settings` row) | — | Must survive restart; DB gives atomic upsert, test isolation, S3 backup |
| Daily scheduling | API / Backend (`daily_ops.run_eod` via scanner loop + boot catch-up) | — | Existing once-per-trading-day, thread-offloaded job |
| Drift alert | API / Backend (`notify.alert`) | — | Telegram is server-side fire-and-forget |
| Flag + "Re-check now" button | Browser / Client (`StrategyPerformancePage.tsx`) | API (GET/POST endpoints) | Display + trigger only; no logic in the browser |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib (`sqlite3`, `json`, `threading`, `re`, `bisect`) | Python 3.14.2 `[VERIFIED: python --version]` | Segment stats, classifier, baseline lock | Repo already does all of this with stdlib |
| FastAPI | `>=0.115` `[VERIFIED: pyproject.toml:11]` | Two new endpoints | Existing server |
| pandas / numpy | `>=2.2` / `>=1.26` `[VERIFIED: pyproject.toml]` | Only if the India replay reuses `strategy_lab._index_path` (returns a `pd.Series`) | Already a dependency; no new install |
| pytest | 9.0.3 `[VERIFIED: python -m pytest --version]` | Backend tests | Existing runner |
| React 19 + `@tanstack/react-query` + `sonner` | `^19.2.6` / `^5.100.14` / `^2.0.7` `[VERIFIED: dashboard/package.json]` | Panel, polling hook, toast on button | Existing dashboard patterns |

### Supporting (existing in-repo helpers to reuse — do NOT duplicate)
| Helper | Location | Use |
|--------|----------|-----|
| `strategy_learning._state / _frozen / OBSERVE_MAX / READY_MIN_DAYS / FREEZE_LOOKBACK` | `index_ai/strategy_learning.py:34-64` | The ladder gate (see Q4) |
| `strategy_performance._india_charges`, `_after_epoch` | `index_ai/strategy_performance.py:68, 33` | Net-of-charges per-trade P&L; data-epoch cut-off |
| `data_epoch()` | `index_ai/data_epoch.py` (value `2026-09-10T02:39:59.830611+05:30`) | Epoch cut-off |
| `day_review._exit_notes / _bucket_exit` | `index_ai/day_review.py:26-40, 128-158` | India exit-reason read-back and bucketing |
| `atomic_io.atomic_write_json` | `index_ai/atomic_io.py` | Only if a JSON file is chosen over `learned_settings` |
| `notify.alert(text, *, key, window_s=3600.0)` | `index_ai/notify.py:159-162` | Drift Telegram |
| `strategy_lab._index_path / _trail_hit / _fill / _quotes / _exit_prices`, `charges.leg_charge_rupees` | `index_ai/strategy_lab.py`, `index_ai/charges.py` | India replay (Q3) |
| `ui/Button` (`pending` prop), `fx.panel/card/cardLabel/cardValue`, `useQuery` hooks, `sonner` toast | `dashboard/src/...` | Panel + button |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `learned_settings` row for the baseline | `memory/exit_recheck.json` via `atomic_write_json` | JSON is not redirected by `tests/conftest.py` (needs explicit monkeypatch) and is not in `cloud_backup._GLOBS` (`cloud_backup.py:38-50`), whereas `trade_memory.sqlite` is both test-isolated and backed up |
| New APScheduler / loop task | A step inside `daily_ops.run_eod()` | `run_eod` already gives once-per-day (`eod_due`), thread offload, boot catch-up, and a daily JSON report; a new loop duplicates all of it |
| Adding a structured `exit_kind` column at close time | Parse `feedback.note` | Writing at close time touches `exit.py` (money path); parsing is read-only. Revisit only if parsing proves lossy |

**Installation:** none. `pyproject.toml` and `dashboard/package.json` need no change.

**Version verification:** N/A — no external packages are added. All versions above are from the repo's own manifests / the local interpreter.

## Package Legitimacy Audit

No external packages are installed by this phase, so the legitimacy gate was not run. **Packages removed due to [SLOP]:** none. **Packages flagged [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```
                       DAILY (15:30 IST+, once / trading day)          BUTTON (any time)
 scanner loop ──> _run_eod_if_due ──> asyncio.to_thread(run_eod)      POST /api/exit-recheck/run
 boot catch-up ─> _eod_catch_up ───────────┘                                   │ await asyncio.to_thread
                                             │                                  │
                                             ▼                                  ▼
                                   daily_ops.run_eod(): ... strategy_learning snapshot,
                                   NEW try/except step ───────────────> exit_recheck.run_recheck()
                                                                                 │
        ┌────────────────────────────────────────────────────────────────────────┤
        ▼                         ▼                              ▼               ▼
 India journal              crypto_journal.jsonl        commodity_journal.jsonl   (India only)
 trade_memory.sqlite        (exit_reason on row)        (exit_reason on row)      market_log.sqlite
 trades + feedback.note     classify -> trail/time/     classify -> stop/         ticks + chain
 (_exit_notes, bucket)      manual/signal/other         manual/square_off/flip    strategy_lab helpers
        └───────────────┬────────────────┴──────────────────────────┘
                        ▼
        segment rows: n, trading days, win%, net ₹ (after charges), trail-hit%,
        ladder state (watching/observing/ready) + frozen flag + (India) replay table
                        ▼
        verdict: n<40 or days<15 -> "not enough data yet" (numbers still shown)
                 ready & frozen  -> "working — no change suggested"
                 ready & !frozen & replay better -> plain-language SUGGESTION (never applied)
                        ▼
        baseline (learned_settings row, under a threading.Lock, absolute values)
        drift = |Δ hit-rate| or |Δ win-rate| > 15 pp  (only when n >= 40)
                        ▼ on transition OK -> DRIFT only
              notify.alert(text, key="exit-drift:<segment>:<baseline_id>")  -> Telegram
              stored result -> GET /api/exit-recheck -> ExitRecheckPanel (pill + table + button)
```

### Recommended Project Structure
```
index_ai/
├── exit_recheck.py      # NEW: segments, classifier, ladder gate, baseline/drift, summary (+ India replay fn)
├── daily_ops.py         # EDIT: one try/except step in run_eod()
├── server.py            # EDIT: GET /api/exit-recheck, POST /api/exit-recheck/run
├── position_exits.py    # EDIT: swap _premium_trailed_credit's predicate (Q1)
├── trailing.py          # EDIT: delete the premium branch (Q1)
└── premium_trail.py     # DELETE
tests/
├── test_exit_recheck.py # NEW
├── test_premium_trail.py# DELETE
└── test_position_exits.py # EDIT (rename one test; assertions unchanged)
dashboard/src/
├── hooks/useExitRecheck.ts                      # NEW (query + mutation)
└── components/pages/StrategyPerformancePage.tsx # EDIT: add ExitRecheckPanel
```

### Pattern 1: Replace the premium-trail predicate, keep behaviour
**What:** `position_exits._premium_trailed_credit` currently = "directional credit action AND premium_trail_enabled(inst)". `premium_trail._CFG` has NIFTY/BANKNIFTY/SENSEX, so it is True for exactly the three indices that `credit_spread.SELL_TRAIL_POINTS` covers. Re-express it on the live owner.
**Example:**
```python
# index_ai/position_exits.py  (replaces lines 25-37; keep _TRAILED_VERTICALS unchanged)
def _index_trailed_credit(trade: dict[str, Any]) -> bool:
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS  # lazy: same as the old import

    action = str(trade.get("action") or (trade.get("signal") or {}).get("action") or "").upper()
    inst = str(trade.get("instrument") or (trade.get("option") or {}).get("instrument") or "")
    return action in _TRAILED_VERTICALS and inst.strip().upper() in SELL_TRAIL_POINTS
```
Update the call at `position_exits.py:96` and the stale comments at lines 25-26 and 85-90 (they describe a quarter-premium target + hard stop that no longer exists; the real owner is the 1:1 index trail in `credit_spread.evaluate_credit_open_trade`). Behaviour is byte-identical for NIFTY/BANKNIFTY/SENSEX (suppressed) and `""`/FINNIFTY (not suppressed) — exactly what `tests/test_position_exits.py:67-78` and `tests/test_position_exits_ema.py` (trades with no `instrument`) assert.

### Pattern 2: Segment definition for the re-check
**What:** The re-check segment is (lane, instrument) for India, one pooled segment for the crypto point trail, one per commodity for MCX. This is NOT the scorecard's (strategy_mode, instrument) grouping — `strategy_performance._india_strategy` splits the buy lane by candlestick pattern, which would shred an already-thin sample. Group by `trade_lane(action)` (`strategy_router.py:33-40`, returns `"buy"|"sell"|"none"`).
**India "trades under today's rule":** select by the distance the trade itself recorded in `trail_meta`, not by date. Sells: `trail_meta["it_points"] == SELL_TRAIL_POINTS[inst]`. Buys: `trail_meta["trail_distance_points"] == instrument.trail_distance_points` and `trail_activation_points == 0.0`. This is robust to the 2026-09-24 vs 2026-09-28 ambiguity in CLAUDE.md and is verified against real rows below (journal shows older buy rows with `40.0/25.0` and `80.0/50.0`, which a date filter would mis-include).
**Crypto "trades under today's rule":** the journal row does not record the distance in force. Eligible rows = `closed_at >= 2026-09-25T12:00Z` (first `point trail:` exit, verified) AND `strategy not in {"ak_roxx_pro", "btc_daily_straddle"}` (ak_roxx_pro has its own wide P&L-% trail, `crypto/lanes.py:179-199`; `btc_daily_straddle` is a separate lane). Optional improvement: stamp the distance onto the exit row going forward (`crypto/lanes.py` `_build_exit_row`, ~line 1241) — flagged as an Open Question because it touches the crypto exit row.
**Commodities:** the "stop distance" is not one number — `commodities/lanes.py:134-144` derives initial/trail/profit-trail percents from each contract's measured daily ATR (`ATR_K_INITIAL_STOP = 0.22`, `ATR_K_TRAIL_ACTIVATE = 0.22`, `ATR_K_TRAIL = 0.40`, `ATR_K_PROFIT_TRIGGER = 0.55`, `ATR_K_PROFIT_TRAIL = 0.14`), cached 24h. The re-check can only report outcomes (stop-exit share, win rate, net), not a single distance to compare.

### Pattern 3: Baseline = a validated snapshot, drift on transition
**What:** (recommendation; semantics are an Open Question for the user) Baseline = the segment's `{trail_hit_rate, win_rate, n, distance, captured_at}` captured the first time the segment passes the ladder (ready) under a given distance. Current = the same metrics over the most recent window (last 40 qualifying trades). Drift when `abs(current - baseline) > 0.15` on either metric and n >= 40. If the distance in force changes (new `it_points`/`trail_distance_points`), the old baseline is invalid and a new one is captured. Alert only on OK -> DRIFT transition (persist `drifted: bool`), plus `notify.alert(key=f"exit-drift:{segment}:{baseline_captured_at}")` as a second guard. A human "accept as new baseline" action is NOT in CONTEXT and is not required; do not add it unless the user asks.

### Anti-Patterns to Avoid
- **Deleting `_premium_trailed_credit` or its call without the Pattern-1 replacement:** silently re-enables regime/EMA/signal-flip exits for the entire sell lane.
- **Reading `exit_reason` out of `option_json`:** it is never there (see Q2); you will get 0 hits.
- **Using `_india_rows()` from `strategy_learning` for the segment grouping:** its key is (strategy_mode, instrument) and it only carries `_pnl/_day/_hour/...`; it carries no exit reason and no per-trade distance.
- **Counting days from `created_at` of ALL trades:** only trades under the current distance count toward the 14/15-day bar.
- **Putting the recompute inside an `async def` without `asyncio.to_thread`:** the replay touches a 41M-row SQLite table; it will stall every dashboard poll.
- **Re-alerting every run while still drifting:** persist the drift state and only alert on the transition.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 40-trade / days / frozen gate | A new threshold set | `strategy_learning._state`, `_frozen`, `OBSERVE_MAX`, `FREEZE_LOOKBACK` | Single source of truth; the reviewer agent checks exactly these |
| Net-of-charges per trade | New charge maths | `strategy_performance._india_charges(trade)` -> `(charges, slippage)` | Same numbers the scorecard shows |
| India exit-reason read-back | New feedback query | `day_review._exit_notes()` (+ `_TS_TAIL` stripping) | Already strips the `@ date, time IST` suffix |
| Atomic state write | `open().write()` | `atomic_io.atomic_write_json` (JSON) or `learned_settings` upsert (`trade_lots.py:60-72`) | Windows `os.replace` PermissionError retry is already handled |
| Telegram de-dup | A "last sent" file | `notify.alert(..., key=...)` | Stamp file `memory/.notify_seen.json`, atomic, thread-safe |
| Index price path for replay | Re-reading ticks | `strategy_lab._index_path(instrument, session)` | Already decodes exchange `ltt` time and clips 09:15-15:30 |
| 1:1 trail walk | New trail logic | `strategy_lab._trail_hit` semantics | Same rule the live lab uses; do NOT touch `trailing.py` logic |
| Leg pricing + charges | New spread pricing | `strategy_lab._fill` (buy at ask / sell at bid) + `charges.leg_charge_rupees` | Real quotes + real Dhan charges, no proxy |

**Key insight:** every number this phase needs already has an owner. The only genuinely new code is the segment/classifier/drift glue and (optionally) the India replay driver.

## Runtime State Inventory (deletion phase)

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `memory/trade_memory.sqlite`: 46 historical trades carry `pt_*` keys in `trail_meta` (`pt_entry` etc.), first 2026-09-10T11:10, last 2026-09-24T13:47 `[VERIFIED: sqlite query this session]`. 14 `feedback` notes read "Hard stop: premium moved…" / "Trailing exit: premium rose|fell…" (legacy exits). Status counts: all 89 trades CLOSED | **None.** No code reads `pt_*` keys after deletion (grep: only `premium_trail.py` and its test). Old rows stay as history. Do not migrate. `day_review._bucket_exit` strings "Trailing exit"/"Hard stop" are matched only in `tests/test_day_review.py:64-65`; leave them (they classify historical notes) |
| Live service config | None — no external service stores the trail config; it is code constants | None — verified by grep (no `.env` key; `.env` not read per CLAUDE.md) |
| OS-registered state | None — no Task Scheduler/pm2 entry names `premium_trail` | None |
| Secrets/env vars | None — `premium_trail._CFG` is hardcoded, no env var | None |
| Build artifacts | `graphify-out/` (graph.json, cache/ast/*.json, GRAPH_REPORT.md) lists `premium_trail` nodes; `.claude/worktrees/sweet-neumann-5fb2f4/` (detached-HEAD worktree) holds a stale full copy of `premium_trail.py` + tests; `__pycache__` may hold a stale `.pyc` (ignored by Python 3.14 without source) | Optionally regenerate the graph (`graphify update`). **The "nothing imports it" verification grep must exclude `.claude/worktrees` and `graphify-out`.** |

**Canonical question — after every repo file is updated, what still has the old name stored/registered?** Only historical journal rows (inert) and the generated graph (cosmetic). Also outside git: Claude's auto-memory notes (below).

**Preserved-history destination is OUTSIDE the repo.** D-04/CLAUDE.md say `memory/strategy-findings.md`, but the repo's `memory/` is **gitignored** (`.gitignore:21: memory/`) and contains no `.md` files (`git ls-files memory` is empty). The file D-04 means is Claude's auto-memory: `C:/Users/User/.claude/projects/C--Richard-Docx-personal-Algo-BNF/memory/strategy-findings.md` `[VERIFIED: read this session]`. That write cannot be committed. The note `standard-trailing-stop-and-profit.md` in the same folder also states, wrongly after this phase, that `premium_trail.py` is "live and working" and "confirmed wired into … `position_exits._premium_trailed_credit`"; update it in the same task. Content to preserve (from `premium_trail.py:1-31` docstring and `_CFG` at lines 40-44):

> Premium trail (removed 2026-10, Phase 3). Richard's spec 2026-08-31 for NIFTY/BANKNIFTY, SENSEX added 2026-09-15 with derived numbers (avg recorded SENSEX entry premium ~₹216; hard_stop_pts/entry-premium ratio ~16% NIFTY, ~20% BANKNIFTY, ~18% average -> hard_stop_pts=39; trail ~40% of hard stop). Config: NIFTY hard_stop 11 / first target 5% / trail 5; BANKNIFTY 100 / 5% / 35; SENSEX 39 / 5% / 16 (premium points). First target 2026-09-23: 5% of entry premium (was 25%). At 25% the trail almost never armed: every one of the 13 trades whose trail armed since 2026-09-10 closed green, every loser had an unarmed trail, winners like +₹1,014 rode to the full hard stop (-₹2,979). Replay of 37 real recorded MTM paths (gross): 25% -> -₹6,914, 7% -> -₹3,313, 5% -> -₹2,225. Superseded 2026-09-24/28 by 1:1 index-point trails (buys 25/55/80, sells 40/100/130).

## Common Pitfalls

### Pitfall 1: `_premium_trailed_credit` looks like dead code but is a live exit-suppression gate
**What goes wrong:** Removing it (or just the import) makes `strategy_exit_reason` evaluate the EMA-flip / CPR-regime / signal-reverse exits for credit spreads on every index.
**Why it happens:** the name says "premium trail"; the effect is "this credit spread's exit is owned by the trail".
**How to avoid:** Pattern 1. Keep `tests/test_position_exits.py::test_premium_trailed_credit_ignores_signal_flip` (renamed) as the regression guard.
**Warning signs:** sell-lane notes like "CPR regime TRENDING_BULL — closing bearish Sell…" appearing in `feedback` after the change (today only buys get those: 7+2+1+1+1 such notes are all "closing … Buy Put/Call").

### Pitfall 2: `evaluate_open_trade` after deletion must keep the `profit_trail` fallback
**What goes wrong:** `if not pt_evaluated and mtm is not None:` (`trailing.py:261`) must become `if mtm is not None:`, not be deleted — D-05 keeps `profit_trail`. Because the premium branch never ran for buys (`pt_evaluated` was always False for them), buy behaviour is unchanged; `tests/test_buy_scalp_trail.py` pins the 25-pt stop and the "option-price trail no longer closes buys early" case.
**How to avoid:** delete lines 232-259 (comment + import + `pt_evaluated` + the branch), dedent the `mtm` fallback, run `tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_profit_trail.py tests/test_credit_spread.py`.

### Pitfall 3: Exit reasons are not where scouting looked, and not uniform across segments
**What goes wrong:** querying `option_json["exit_reason"]` returns nothing.
**Facts (all `[VERIFIED]` by reading code + real rows):** see Q2 table below. India: `feedback.note`. Crypto/commodities: `exit_reason` on the JSONL row. Three vocabularies; `day_review._bucket_exit` has no "manual close" bucket (returns "other") and `crypto/day_review._bucket_exit` does not recognise `point trail:` (returns "other") although that is now crypto's main exit.
**How to avoid:** one small ordered classifier per segment kind in `exit_recheck.py`, unit-tested with the verbatim real strings below; unknown -> "other" (never dropped).

### Pitfall 4: Thresholds disagree across the codebase
D-03 says "40+ trades and 14+ trading days … same bar used everywhere". Reality: `strategy_learning.py:36` `READY_MIN_DAYS = 15`; `strategy_lab.py:63` `MIN_TRADES, MIN_DAYS = 30, 14`; `strategy_performance.py:326-327` `CRYPTO_LIVE_MIN_TRADES = 30`, `CRYPTO_LIVE_MIN_DAYS = 14`. See Open Question 1.

### Pitfall 5: Replay validity — non-trail exits and wider stops
The live exit is not only the trail. Buys also close on regime/signal flips (`feedback` notes: "CPR regime TRENDING_BULL — closing bearish Buy Put." x7 etc.), manual close (x6), and the 15:10 square-off. A **narrower** distance can only change a trade if it would have fired before the real exit (replayable). A **wider** distance on a trade the trail actually ended extends the hold past the real exit; for buys the real system would also have applied regime-flip exits during that extension, which cannot be replayed from ticks. For sells those flip exits are suppressed (Pitfall 1), so sell replay is cleaner (remaining sell exits: index trail, `max_loss`, short-strike breach, Supertrend, EOD). Treat buy-lane wider-stop numbers as approximate and label them so. Mandatory sanity check: replaying the CURRENT distance must reproduce the actual exit time/reason for most trades; report the match rate next to any suggestion and refuse to suggest when it is low.

### Pitfall 6: Two rows lack `closed_at`
2 of 89 trades have `pnl` but no `closed_at` (`exit_inferred_from_pnl` appears on 2 rows). Replay must skip a trade without `closed_at`/`exit_index_price` and count it as "could not replay".

### Pitfall 7: `notify` stamp TTL caps de-dup at 3 days
`_STAMP_TTL_S = 3 * 86_400.0` (`notify.py:36`) prunes stamps older than 3 days (`_mark_sent`), so a `window_s` > 3 days is meaningless. "One message per drift event" must therefore come from the persisted OK->DRIFT transition, with the `notify.alert` key as the belt-and-braces.

### Pitfall 8: Concurrent daily run + button
Both run in worker threads and both read-modify-write the baseline row. Use one module-level `threading.Lock` around the whole recompute-and-store (pattern: `trade_lots._LOTS_LOCK`, `commodities/lanes._STATE_LOCK`). Compute from journals, then store absolute values — never deltas.

## Code Examples

### Q1 evidence — why the premium branch cannot run
```python
# index_ai/trailing.py:176-177 [VERIFIED]
def _is_long_premium(action: str, transaction_type: str) -> bool:
    return action.upper() in ("BUY_CALL", "BUY_PUT") and transaction_type.upper() == "BUY"

# index_ai/trailing.py:189-196 [VERIFIED] -- every 2+-leg spread leaves here, before line 235
    option = trade.get("option") or {}
    if is_credit_option(option):
        return evaluate_credit_open_trade(trade, current_index_price, risk, fresh_supertrend=fresh_supertrend)

# index_ai/trailing.py:244 [VERIFIED] -- buys are excluded here
    if premium_trail_enabled(instrument_key) and not _is_long_premium(action, tx):
```
`is_credit_option` is `len(legs) >= 2 and all(leg.get("security_id") is not None ...)` (`credit_spread.py:22-24`). `credit_spread.py:346`: `SELL_TRAIL_POINTS = {"NIFTY": 40.0, "BANKNIFTY": 100.0, "SENSEX": 130.0}`. `instruments.py:104,115,126`: `**_buy_scalp_trail(25.0)`, `(55.0)`, `(80.0)`.

**Reachability matrix (buy/sell x index):**

| Lane / index | Path in `evaluate_open_trade` | Premium branch executed? |
|---|---|---|
| Buy (BUY_CALL/BUY_PUT, tx BUY) NIFTY, BANKNIFTY, SENSEX | passes `is_credit_option` (single leg) -> `_is_long_premium` True -> condition at line 244 False | **No** |
| Sell (SELL_BULL_PUT_SPREAD / SELL_BEAR_CALL_SPREAD) NIFTY, BANKNIFTY, SENSEX | `is_credit_option` True -> early return at line 190-196 | **No** |
| Sell iron condor | same early return | **No** (also none in journal) |
| Naked SELL_ATM_PUT/CALL (single leg) | would reach line 244 with `not long` True | Only if such a trade can exist: `plan_builder.py:40-52` builds it only when `apex_use_hedged_spreads` is False (default True, `strategy_params.py:24,143`), AND a signal with that action must be emitted — grep of `sell_strategy.py`, `strategy_router.py`, `planner.py`, `strategy.py` finds **no emitter** (only `options_cpr/` backtest tooling and `tests/test_brain.py:144`). Naked single-leg selling was also ruled out by Richard (memory directive: "drop the naked 1-leg path"). **Unreachable in practice; behaviour for it after deletion would fall to the `profit_trail` fallback.** |

Empirical: of 89 journal trades, `pt_entry` appears in 46, the last created 2026-09-24T13:47+05:30; 0 of 62 spreads lack leg `security_id`; all 89 trades are CLOSED (no open row can resurrect it).

### Complete deletion surface (file:line)
| # | File | Change |
|---|------|--------|
| 1 | `index_ai/premium_trail.py` | DELETE (216 lines) |
| 2 | `tests/test_premium_trail.py` | DELETE (68 lines, 6 tests) |
| 3 | `index_ai/trailing.py:232-259` | DELETE comment + `from index_ai.premium_trail import (...)` + `pt_evaluated` + premium `if` block; line 261 `if not pt_evaluated and mtm is not None:` -> `if mtm is not None:`; fix the comment at 261-263 (keep: it is the D-05 fallback). `pivot_target` no longer read here |
| 4 | `index_ai/position_exits.py:25-37, 85-90, 96` | Pattern 1 replacement + docstring/comment refresh (stale: "quarter-premium target, hard stop") |
| 5 | `index_ai/entry_guard.py:18-19` | docstring sentence "letting ``premium_trail`` own the exit…" -> "the 1:1 index trail (``credit_spread.SELL_TRAIL_POINTS``)" |
| 6 | `tests/test_position_exits.py:67-78` | rename test (e.g. `test_index_trailed_credit_ignores_signal_flip`) + comments "premium_trail"/"premium-trail params"; assertions unchanged |
| 7 | Comments only: `index_ai/planner.py:34` ("arms the premium trail early" — `_pivot_target` still feeds `option["pivot_target"]` -> `brain/features.py:157` ML feature `dist_pivot_target_pct`; **do not delete the function**), `index_ai/instruments.py:58` ("credit spreads have their own premium trail"), `index_ai/strategies/option_structures.py:71` ("the premium trailing stop (100 pts on the short…)"), `index_ai/strategies/credit_spread.py:390` ("replaces the short-leg premium trail"; still true historically, harmless), `tests/test_credit_spread.py:154` section heading ("premium trail on the short leg") |
| 8 | `dashboard/src/lib/strategies.ts:196` | string `'CPR + EMA + Supertrend + OI · premium-trail exits'` -> index-point-trail wording; requires `npm --prefix dashboard run build` |
| 9 | `CLAUDE.md:62-68` | rewrite: "premium_trail.py … still exists" -> removed; keep the point values |
| 10 | `.planning/codebase/CONCERNS.md:14-17,89`, `STRUCTURE.md:60` | planning docs; update/mark resolved |
| 11 | `guides/Strategy Guide.md:166-178` | not a premium_trail reference, but a THIRD contradictory trail description ("Initial stop 100/200 pts, arm after +25/+50, trail 40/80 … long premium only; index-point trails apply only to long premium, not credit spreads"). Success criterion "one trailing-stop implementation" is hollow if the guide still documents a different one; replace with the real 25/55/80 and 40/100/130 1:1 rule |
| 12 | Claude auto-memory (outside git): `strategy-findings.md` (add preserved history), `standard-trailing-stop-and-profit.md` (premium-trail bullet + profit_trail sentence) | see Runtime State Inventory |
| 13 | New regression test | `importlib.util.find_spec("index_ai.premium_trail") is None` and a source scan of `index_ai/`+`tests/` for `premium_trail` identifiers (exclude `.claude/worktrees`, `graphify-out`) |

### Q2 — where exit reasons live (all `[VERIFIED]`)
```python
# index_ai/exit.py:390,401  -- the close path
    note = f"{reason} @ {format_ist_display(now_ist_iso())}"
    ...
    learned = record_trade_outcome(trade_id, pnl, note=note)
# index_ai/learning.py:1419-1422  -- writes it to the feedback table, NOT option_json
        db.execute(
            "INSERT INTO feedback (trade_id, rating, note, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, rating, note or f"Outcome PnL: {pnl}", now_utc()),
        )
```
`option_json` on close only gains `closed_at`, `exit_index_price`, `exit_ltp`, `leg_exit_ltps`, `mtm_history`, and the last `trail_meta`. Real distinct India note shapes (90 feedback rows for 89 trades; 0 trades have >1 row; 0 null `trade_id`):

| Real note (numbers elided) | Count | Classify as |
|---|---|---|
| `Index trail: # crossed the stop # (# pts behind best #; # pts from entry).` | 26 | trail stop (sell lane) |
| `Trailing stop armed — # index pts behind peak at #` | 4 | trail stop (buy lane; message when the trail armed at 0-pt activation) |
| `Initial stop (# index pts) hit at #` | 1 | trail stop (buy lane, initial) — note `day_review._bucket_exit` returns "other" for this one |
| `Hard stop: premium moved …` / `Trailing exit: premium rose|fell …` | 12 / 10+2 | legacy premium trail (pre-2026-09-24; excluded by the distance filter) |
| `End-of-session square-off (IST)` | 13 | time exit |
| `manual close` | 6 | manual (day_review bucket returns "other") |
| `CPR regime …`, `AUTO: CPR …`, `Strategy signal … — closing …` | 7+2+1+1+1 | signal exit |
| `Price # above|below Supertrend stop #` | 3 | signal exit (Supertrend) |
| `Index # below short put #` | 1 | short-strike breach (treat as stop-type "other") |

Crypto `exit_reason` (248 rows, all paper; first row 2026-09-12): `manual close` 80, `point trail: # pts behind best # (stop #, # pts from entry)` 28, `trailing stop #% P&L (peak…)` 27 and `trailing profit #% P&L …` 22 (older P&L-% trail), `session end` 20, `close # back through SMA# … band` 33, `trend flip` 11, `15m N` / `15m inverted-N` 12, `Close … back above Kijun` 5, `N-day max hold` 3, plus singletons. Per-row extras usable for stats: `peak_pnl_pct`, `trail_stop_pnl_pct`, `strategy`, `asset`, `pnl_usd`, `fees_usd`, `day`, `closed_at`.
Commodities `exit_reason` (27 rows, 2026-09-14..17): `stop` 18, `manual close` 5, `square_off` 3, `trend_flip` 1 (`commodities/lanes.py:247`); extras `peak_price`, `trail_armed`, `profit_armed`, `trail_stop_at_exit` (`lanes.py:255-258`) distinguish initial stop vs trail vs profit-trail. Note a commodity `stop` exit fills at `pos["stop"]`, not the mark (`lanes.py:337`).

**Consistency verdict:** NOT consistent. Normalise into {trail_stop, time_exit, manual, signal_exit, other} per segment kind. Hit-rate definition for D-06: `trail_hit_rate = closed trades whose exit class is trail_stop / all closed trades in the window`. Win rate: net (after charges for India via `_india_charges`; crypto `pnl_usd` and commodities `net_rupees` are already net) `> 0`.

```python
# exit_recheck.py -- classifier skeleton (ordered; unknown -> "other"; strip " @ ..." for India)
import re
_INDIA = (
    ("manual", re.compile(r"^manual close", re.I)),
    ("time_exit", re.compile(r"square-?off|end-of-session", re.I)),
    ("trail_stop", re.compile(r"^index trail:|^trailing stop armed|^initial stop \(", re.I)),
    ("legacy_premium", re.compile(r"^hard stop: premium|^trailing exit: premium", re.I)),
    ("signal_exit", re.compile(r"regime|strategy signal|^auto:|supertrend", re.I)),
)
_CRYPTO = (("trail_stop", re.compile(r"^point trail:|^trailing (stop|profit)", re.I)),
           ("manual", re.compile(r"^manual close", re.I)),
           ("time_exit", re.compile(r"^session end|max hold", re.I)))  # everything else -> signal_exit/other
_COMMODITY = {"stop": "trail_stop", "manual close": "manual", "square_off": "time_exit", "trend_flip": "signal_exit"}
```

### Q3 — what can be replayed, and what cannot (all counts `[VERIFIED]` by querying read-only)
**Data that exists** (`memory/market_log.sqlite`): `ticks` with `instrument` NIFTY/BANKNIFTY/SENSEX in 32 sessions, 2026-08-29..2026-10-03 (index `idx_tick_session`); `chain` (real strike-level bid/ask/ltp, strikes near spot, ~1 snapshot/min) in only **8 sessions, 2026-09-23..2026-10-01**; `observations` (spot ~90 s resolution) 2026-08-31..2026-10-01. Per-trade, the journal holds: entry time (`created_at`), `trail_meta.entry_index_price`, leg `security_id`s/strikes, `mtm_history` (option price ~every 30-45 s but ONLY until the real exit, and no index price), `exit_index_price`, `closed_at`.
- **India: replay feasible** for a different 1:1 distance: walk `_index_path` ticks from entry (reuse `_trail_hit` anchor logic) to find the alternative exit time or 15:10; price the legs at that time from the nearest chain snapshot with `_fill` + `leg_charge_rupees`; compare to the real outcome. Limits: (a) rupee pricing needs chain rows, so trades before 2026-09-23 are not priceable (they can only be compared in index points, which is not rupees — do not present points as rupees); (b) a widened stop can run the index past the recorded strike window, leaving the leg unquoted -> count as "could not price", do not interpolate; (c) tick delivery lag spikes (`strategy_lab._index_path` docstring: 7 min on 2026-09-16, 29 min on 2026-09-17) — use exchange `ltt`, which `_index_path` already does; (d) Pitfall 5 (non-trail exits, buy-lane wider stops) and Pitfall 6.
- **Crypto: NOT replayable from live data.** The live lane fetches candles per scan and does not store them (`crypto/candle_cache.py` docstring: "deliberately NOT wired into the live scanning path", "for backtests/research only"; the cache has 1h bars for 6 symbols). Per trade only entry/exit price and `peak_pnl_pct`/`trail_stop_pnl_pct` exist — no path, so no honest counterfactual for another distance. Using downloaded Delta candles to replay would violate the standing "live data only, no downloaded-history as ground truth" rule (`strategy-analysis-and-simplification-directive.md`, 2026-09-12). **Honest output: stats + drift only, verdict "no replay data yet".**
- **Commodities: NOT replayable** (no stored price path; stop distances are ATR-scaled per contract per 24 h and not stored on the row). Same honest output.
- `strategy_lab.run_session` has the trail distance as a local (`trail_dist`, `strategy_lab.py:523-525`, derived from `BUY_TRAIL_POINTS`/`SELL_TRAIL_POINTS`). Swapping a distance for the *lab's* candidates needs one optional parameter (`trail_dist: float | None = None`). But the lab replays *lab candidates'* entries on the recorded chain, not the live journal's trades, so it is supplementary evidence at most; D-02/D-03 are about live-journal trades. Do not wire a lab verdict into the suggestion.
- Candidate alternative distances are a design choice not fixed by CONTEXT (`[ASSUMED]`: current x {0.75, 1.25, 1.5}, rounded to the index's tick step) — see Open Questions.

**Trade counts right now (closed trades, data epoch 2026-09-10; "under today's distance" = trade's own recorded `it_points` / `trail_distance_points`):**

| Segment | All closed | Under today's distance | Trading days (current) | Net ₹ (gross, current) |
|---|---|---|---|---|
| India buy NIFTY | 14 (8 d) | 3 | 3 | -975 |
| India buy BANKNIFTY | 9 (6 d) | 4 | 2 | -1,374 |
| India buy SENSEX | 4 (4 d) | 3 | 3 | -997 |
| India sell NIFTY | 20 (13 d) | 10 | 5 | -455 |
| India sell BANKNIFTY | 23 (13 d) | 10 | 4 | +2,301 |
| India sell SENSEX | 19 (10 d) | 9 | 4 | -104 |
| Crypto 1.6% point trail (excl. `ak_roxx_pro`, `btc_daily_straddle`) | 49 since 2026-09-25 | 49 (cpr_trend 24, ny_n_break 15, ichimoku 7, rsi_adx_trend 3) | <= 8 | USD, net of fees |
| Crypto `ak_roxx_pro` (own wide trail) / `btc_daily_straddle` | 17 / 2 | n/a (different trail) | — | — |
| Commodities (CRUDEOILM 11, SILVERMIC 7, NATGASMINI 6, GOLDM 3) | 27 | 27 | 4 (last trade 2026-09-17) | INR net |

Every India segment is far below 40 trades / 15 days -> "not enough data yet" with the numbers still shown. Crypto is the closest to the days bar (8 of 14-15). The sell lane's 9-10 trades each at 4-5 days will take weeks. Index futures (`memory/futures_journal.jsonl`, 30 rows) are NOT named in D-01 and are out of scope unless the user says otherwise.

### Q4 — confidence ladder: reuse exactly
```python
# index_ai/strategy_learning.py:34-47, 62-64 [VERIFIED]
WATCH_MAX = 15  # below this: collect only
OBSERVE_MAX = 40  # below this: observe, no suggestions
READY_MIN_DAYS = 15  # …and this many distinct trading days
FREEZE_LOOKBACK = 20  # net-positive over the last N recent trades → frozen
def _state(n_trades: int, n_days: int) -> str:
    if n_trades < WATCH_MAX:
        return "watching"
    if n_trades < OBSERVE_MAX or n_days < READY_MIN_DAYS:
        return "observing"
    return "ready"
def _frozen(pnls_recent: list[float]) -> bool:
    """Net-positive over the recent window → locked, the tuner won't touch it."""
    return len(pnls_recent) >= 5 and sum(pnls_recent) > 0
```
Use: `state = _state(n, days)`; `frozen = _frozen(pnls_newest_first[:FREEZE_LOOKBACK])` (note `_report_for` assumes newest-first, `strategy_learning.py:201`); a suggestion is allowed only when `state == "ready" and not frozen`. **Yes, frozen is relevant to a stop-distance SUGGESTION:** a stop distance is a numeric strategy parameter, and `.claude/agents/strategy-tuning-reviewer.md` rule 2 states "Frozen means untouchable … checked before any suggestion or auto-apply, not just surfaced as a UI label" (rule 1: ladder gates on trades AND days; rule 3: suggest != apply; rule 4: no backtest data as proof). So frozen -> still SHOW the numbers and the drift monitor, but emit no recommendation text ("working — no change suggested"). Drift monitoring is a monitor, not a tuner, so it is not gated by frozen. `_india_rows()`/`learning_report()` in that module cannot be reused for grouping (Pattern 2), but import its constants/`_state`/`_frozen` (private-underscore names are already imported across modules in this repo, e.g. `strategy_learning` imports `strategy_performance._after_epoch`).

### Q5 — scheduling, alert, persistence
- **Hook:** add a step to `daily_ops.run_eod()` after the strategy-learning snapshot (`daily_ops.py:229-234`) in the file's own style:
```python
    try:  # daily stop-distance re-check + drift watch (suggest-only)
        from index_ai.exit_recheck import run_recheck

        report["exit_recheck"] = run_recheck(trigger="daily")
    except Exception as exc:
        report["exit_recheck"] = {"error": str(exc)[:200]}
```
`run_eod` is already invoked as `await asyncio.to_thread(run_eod)` by `scanner._run_eod_if_due` (`scanner.py:402-418`) and by `server._eod_catch_up` (`server.py:377-396`), gated once per IST trading day by `eod_due()` (`daily_ops.py:155-167`: trading day, after `session_times()["market_close"]`, `eod_date` state). Time = India market close (15:30 IST) — satisfies D-08 for India; crypto/commodities ride the same run (crypto's own nightly loops are `_crypto_nightly_loop` once per UTC day and `_crypto_day_summary_loop` at 23:58 IST, `server.py:268-353`; do not add a third). Trade-off to state: no daily run on NSE holidays/weekends; the button covers it.
- **Alert (D-07):** template is `reconcile.py:133-135`: `notify.alert(text, key=f"reconcile:{sid}:{issue['kind']}")`, wrapped in `try/except Exception: pass`, imported lazily. Use `key=f"exit-drift:{segment_id}:{baseline_captured_at}"`; remember Pitfall 7 (3-day stamp TTL) so the persisted transition flag is the primary de-dup. `notify.send` spawns a daemon thread (`notify.py:156`), so it never blocks. Plain-language text, no jargon (Richard is non-technical; `talk-in-plain-language` memory).
- **Baseline persistence:** a `learned_settings` row (`key TEXT PRIMARY KEY, value_json, updated_at`), upsert pattern `trade_lots.py:60-72` (`INSERT … ON CONFLICT(key) DO UPDATE`), key `exit_recheck_state`, inside one `threading.Lock`. Survives restart, is in `trade_memory.sqlite` (backed up by `cloud_backup._SQLITE`), and `tests/conftest.py` already redirects `index_ai.learning.DB_PATH` per test. The JSON alternative would use `atomic_write_json` (retries Windows `PermissionError`) under a lock, plus an explicit test monkeypatch of its path.
- **Blocking I/O rule:** endpoints use `await asyncio.to_thread(fn)` (e.g. `server.py:1573`); the POST must also guard double-clicks server-side (lock + `pending` button), as `Button` already disables while pending.

### Q6 — dashboard
- Tab = "Strategy P&L" (`App.tsx:192`, page `components/pages/StrategyPerformancePage.tsx`, mounted `App.tsx:388`). Panels are `<section className={cn(fx.panel, 'p-4')}>` (`LearningPanel`, lines 18-91). State pills reuse a `STATE_STYLE` map with `var(--warn)` / `var(--up)` / `var(--down)` tokens; mono/tabular numbers via `font-mono`/`fx.cardValue`. Insert `<ExitRecheckPanel />` between `<RiskManagerPanel />` and `<LearningPanel />` (lines 141-143).
- Hook pattern: `useStrategyLearning.ts` (`useQuery` + `usePollMs(60_000, enabled)` + `keepPreviousData`). Button pattern: `FeaturesPanel.tsx:29-41` (`useMutation` -> `api(path, {method:'POST', body})`, `toast.success/error` from `sonner`, `qc.invalidateQueries`, `<Button pending={mutation.isPending}>`). `api()` is `lib/api.ts`.
- API: model GET on `server.py:1567-1575` (`/api/strategy-performance`, `include_in_schema=False`, `await asyncio.to_thread`). The new POST is non-financial like `POST /api/settings/features` (no `require_admin_secret`; whole `/api` is behind `DASHBOARD_PASSWORD` basic auth when set).
- Dates: any day math must use `lib/ist.ts`; prefer rendering server-supplied strings (do not add `new Date()`).
- Tests: `dashboard/package.json` scripts are `dev`, `build` (`tsc -b && vite build`), `lint`, `preview` only; **no JS test runner** exists. Verification = `npm --prefix dashboard run build` + `npx tsc --noEmit` + `npm --prefix dashboard run lint` + a manual look (`.claude/launch.json` has an `algo-bnf` and a `dashboard` config).
- Mandatory reviewer after dashboard edits: `.claude/agents/ui-consistency-reviewer.md` — tokens not raw colours, no `rounded-xl/2xl/lg` on panels, numbers `font-mono tabular-nums`, `fx.panel/fx.card` not hand-rolled, IST via `lib/ist.ts`, reuse `StatTile/TradeLogTable/PeriodBar` where applicable.

### Q7 — crypto and commodities stop code
- **Crypto:** `crypto/strategies/trailing.py` `TrailConfig.point_trail_pct` (default field 0.0, line 41); live value `point_trail_pct=max(0.0, _f("CRYPTO_POINT_TRAIL_PCT", 1.6))` (`crypto/config.py:233`), read fresh per scan and handed to every strategy by `lanes._trail_cfg` (`lanes.py:168-176`). Stop = `entry * pct/100` from the best price, 1:1; `_point_trail` exit text `f"point trail: {dist:,.6g} pts behind best {best:,.6g} (stop {stop:,.6g}, {locked:+,.6g} pts from entry)"` (line 73-74). Exceptions: `ak_roxx_pro` uses `_ak_roxx_trail` (P&L-% -55/15/100/15, `lanes.py:179-199`); the exchange bracket stop is `bracket_stop_price` (same fraction). The evaluated number is the single global `1.6` -> pool across strategies/coins; per-coin splits are informational only.
- **Commodities:** `commodities/lanes.py` uses the shared two-phase `index_ai/strategies/futures/price_trail.py` (`update_price_trail`, `PriceTrailLevels`), distances = percent-of-entry from ATR-scaled `CommoditySpec` fields (see Pattern 2). It does NOT share `index_ai/trailing.py`. Journal: `memory/commodity_journal.jsonl`.
- Neither has per-trade price paths; both only support stats + drift (Q3).

### Q8 — tests
| Test file | Action |
|---|---|
| `tests/test_premium_trail.py` | **Delete** (imports `index_ai.premium_trail`; 6 tests) |
| `tests/test_position_exits.py` (`test_premium_trailed_credit_ignores_signal_flip`, lines 67-78) | **Update**: rename + comments; assertions stay (BANKNIFTY, SENSEX suppressed; FINNIFTY not) |
| `tests/test_position_exits_ema.py` | no change (uses trades with no `instrument` -> predicate False) |
| `tests/test_credit_spread.py` | comment-only (`_bn_bear_call`/`_bn_trade` are still used at lines 273-280; do NOT delete them) |
| `tests/test_buy_scalp_trail.py`, `test_trailing.py`, `test_profit_trail.py`, `test_fast_trail_loop.py`, `test_tick_driven_stops.py`, `test_exit_credit.py`, `test_strategy_lab.py` | no change; must stay green (they pin the live rules) |
| `tests/test_strategy_learning.py`, `test_strategy_performance.py`, `test_day_review.py` | patterns to copy (monkeypatch `learning.recent_trades`, `sl.data_epoch`) |
| New `tests/test_exit_recheck.py` | follow `tests/test_reconcile_fault_injection.py:310-377`: `monkeypatch.setattr("index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key)))` |

Baseline run this session: `python -m pytest tests/test_premium_trail.py tests/test_position_exits.py tests/test_position_exits_ema.py tests/test_trailing.py tests/test_buy_scalp_trail.py tests/test_credit_spread.py tests/test_fast_trail_loop.py tests/test_tick_driven_stops.py tests/test_exit_credit.py tests/test_strategy_lab.py tests/test_strategy_learning.py tests/test_strategy_performance.py tests/test_crypto_point_trail.py tests/test_day_review.py -q` -> **91 passed in 9.87 s** (a good ~10 s quick command for the deletion). Isolation: `tests/conftest.py` autouse redirects `.env`, `index_ai.learning.DB_PATH`, `index_ai.market_log.DB_PATH`, spread samples and clears Telegram env; it does **not** redirect `crypto.journal.JOURNAL_PATH`, `commodities.lanes.JOURNAL_PATH`, `memory/` JSON state, or `data_epoch` — new tests must monkeypatch those to `tmp_path` (see `tests/test_strategy_performance.py:120` for the crypto pattern) and must never touch the real journals or send Telegram.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| % of option premium trail (`premium_trail.py`, 5% first target) | 1:1 index-point trail (buys 25/55/80, sells 40/100/130) | 2026-09-24 (sells/buys), 2026-09-28 (buys scalp 0-activation + Supertrend off for buys) | Premium trail never runs; file is dead weight and a re-enable hazard |
| crypto P&L-% ratchet trail | `point_trail_pct` 1.6% 1:1 | 2026-09-25 | `point trail:` reasons; old rows still carry `trailing stop/profit` reasons |
| Black-Scholes proxy backtests | Real recorded chain/ticks only | 2026-09-23 | Replays here may only use `market_log` data |
| Percent-based slippage guess | Measured spread (`spread_calib`) | 2026-09-24 | Use `_india_charges`, not old numbers |

**Deprecated/outdated:** `premium_trail.py` (this phase); `guides/Strategy Guide.md` trailing section; `crypto/day_review._bucket_exit` lacks `point trail`; `day_review._bucket_exit` lacks "manual close".

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Baseline semantics: "last validated" = first ladder-ready snapshot per (segment, distance), compared against the most recent 40-trade window; re-baselined when the distance changes; no human-accept step | Pattern 3 | If Richard expects a rolling "yesterday vs today" or an explicit approve-baseline button, drift would fire/not fire differently |
| A2 | Candidate alternative distances = current x {0.75, 1.25, 1.5}, rounded to a tick step | Q3 | Grid is invented; a different grid changes what "30 would have kept ₹X more" can say |
| A3 | Crypto is evaluated as ONE pooled segment (global 1.6%) | Pattern 2 / Q7 | Per-coin or per-strategy view may be wanted; pooled hides a coin where 1.6% is wrong |
| A4 | Index futures journal is out of scope (D-01 names only India options, crypto, commodities) | Q3 | If "every segment" includes futures, one more journal reader is needed |
| A5 | A daily run only on NSE trading days (via `eod_due`) is acceptable for crypto/commodities | Q5 | Crypto drift could go unnoticed over a long weekend; the button mitigates |
| A6 | Replay match-rate refusal threshold (e.g. require the replay at the current distance to reproduce the real exit for a clear majority of trades before suggesting) | Pitfall 5 | Number is a judgement call |

## Open Questions

1. **14 vs 15 trading days.** D-03 says 14; the reusable ladder (`strategy_learning.READY_MIN_DAYS`) and the reviewer agent say 15; the lab and crypto readiness use 30 trades/14 days.
   - What we know: D-03 also says "same bar used everywhere else", so intent = the ladder.
   - Recommendation: call `strategy_learning._state()` directly (single source of truth, 40/15); tell Richard it is one day stricter than the wording of D-03 and ask only if he objects. No segment is near either bar today, so the choice is not urgent.
2. **Baseline semantics (A1).** Confirm or adjust before building the drift comparison. Recommendation as in Pattern 3.
3. **Crypto/commodities "suggest a different distance".** No recorded path exists, so those segments can only say "no replay data yet" for D-02. Is that acceptable for "Everything", or does Richard want path recording added (new data collection, e.g. per-position price extremes in the crypto/commodity exit row), which is a separate piece of work touching the crypto exit row? Recommendation: accept the honest message now; list path recording as a follow-up, not Phase 3.
4. **Stamp the active crypto distance on the exit row going forward?** Makes "trades under today's rule" exact instead of date-based. Touches `crypto/lanes.py` `_build_exit_row` (journal only, not the order path). Recommendation: skip unless the user wants exactness; use the date + strategy filter.
5. **India replay scope.** It is the heaviest piece (ticks + chain + `strategy_lab` reuse, validity checks). Recommendation: Wave 0 feasibility spike reproducing, at the CURRENT distance, the real exits of the ~35 trades since 2026-09-25 before committing to the full comparison UI; if the match rate is poor, ship stats + drift + "replay not reliable yet" and keep the replay as a CLI/diagnostic.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all backend | ✓ | 3.14.2 | — |
| pytest | tests | ✓ | 9.0.3 | — |
| ruff | lint | ✓ | 0.15.12 | — |
| Node / npm | dashboard build | ✓ | v24.16.0 / 11.13.0 | — |
| `memory/trade_memory.sqlite`, `crypto_journal.jsonl`, `commodity_journal.jsonl`, `market_log.sqlite` | re-check data | ✓ (read-only; 89 / 248 / 27 rows; ticks 41M rows) | — | — |
| Telegram bot token (`.env`) | live alert | not checked (`.env` is blocked) | — | Tests monkeypatch `notify.alert`; conftest clears the token |
| Running server on :8000 + `DASHBOARD_PASSWORD` | manual button check | not checked | — | User reads the password from `.env`; Claude cannot call the API directly |

**Missing dependencies with no fallback:** none. **Missing with fallback:** none blocking.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 (`[tool.pytest.ini_options] testpaths = ["tests"]`, `pyproject.toml`) |
| Config file | `pyproject.toml`; shared isolation in `tests/conftest.py` (autouse) |
| Quick run command | `python -m pytest tests/test_exit_recheck.py tests/test_position_exits.py tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_credit_spread.py tests/test_profit_trail.py tests/test_fast_trail_loop.py tests/test_tick_driven_stops.py tests/test_exit_credit.py tests/test_strategy_learning.py tests/test_strategy_performance.py tests/test_day_review.py -q` (~10 s without the new file) |
| Full suite command | `python -m pytest -q` (660 tests, ~7-8 min) |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXIT-02 | `index_ai.premium_trail` no longer importable; no `premium_trail` identifier in `index_ai/` or `tests/` | unit/source-scan | `python -m pytest tests/test_exit_recheck.py::test_premium_trail_is_gone -q` | ❌ Wave 0 |
| EXIT-02 | Sell-lane credit spread (NIFTY/BANKNIFTY/SENSEX) still ignores signal flip / regime exit; FINNIFTY still closes on it | unit | `python -m pytest tests/test_position_exits.py -q` | ✅ (rename test) |
| EXIT-02 | Buy trail unchanged (25/55 pt, from first point, no option-price early close); evaluate_open_trade works with premium branch removed | unit | `python -m pytest tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_profit_trail.py tests/test_credit_spread.py -q` | ✅ |
| EXIT-01 | Exit classifier maps every verbatim real India/crypto/commodity reason to the right class; unknown -> other | unit | `python -m pytest tests/test_exit_recheck.py::test_exit_classifier -q` | ❌ Wave 0 |
| EXIT-01 | Segment builder: India lane x instrument; "under today's distance" filter by `it_points`/`trail_distance_points`; crypto excludes ak_roxx_pro/btc_daily_straddle; epoch cut-off | unit | `python -m pytest tests/test_exit_recheck.py::test_segments -q` | ❌ Wave 0 |
| EXIT-01 | Verdict: <40 trades or <15 days -> "not enough data yet" with numbers still returned; ready+frozen -> no suggestion; ready+not frozen+replay better -> plain-language suggestion, never applied | unit | `python -m pytest tests/test_exit_recheck.py::test_verdict_ladder -q` | ❌ Wave 0 |
| EXIT-01 | India replay at the current distance reproduces a synthetic real-looking trade; missing chain quote -> "could not price" | unit (tmp `market_log` like `tests/test_strategy_lab.py`) | `python -m pytest tests/test_exit_recheck.py::test_india_replay -q` | ❌ Wave 0 |
| EXIT-03 | Drift: >15 pp on hit-rate or win-rate AND n>=40 -> DRIFT; exactly 15 or n<40 -> OK; baseline persists across a simulated restart; distance change re-baselines | unit | `python -m pytest tests/test_exit_recheck.py::test_drift -q` | ❌ Wave 0 |
| EXIT-03 | Alert fires once on OK->DRIFT, not on the next run while still drifting, fires again after recovery+new drift; key shape | unit (monkeypatch `index_ai.notify.alert`) | `python -m pytest tests/test_exit_recheck.py::test_drift_alert_once -q` | ❌ Wave 0 |
| EXIT-01/03 | `run_eod` includes `exit_recheck`; failure inside it never breaks the report | unit | `python -m pytest tests/test_exit_recheck.py::test_run_eod_hook -q` | ❌ Wave 0 |
| EXIT-01/03 | GET/POST endpoints return the stored/fresh result; POST runs off the event loop | API (`TestClient(app)`, pattern `tests/test_admin_auth.py`) | `python -m pytest tests/test_exit_recheck.py::test_endpoints -q` | ❌ Wave 0 |
| EXIT-03 | Dashboard flag + button build and type-check | build | `npm --prefix dashboard run build` ; `npx tsc --noEmit` (run in `dashboard/`) | n/a (no JS test runner) |

### Sampling Rate
- **Per task commit:** the quick command above (touching only deletion: the ~10 s legacy set; touching `exit_recheck.py`: add `tests/test_exit_recheck.py`).
- **Per wave merge:** quick command + `ruff check index_ai/` (compare to the ~8 pre-existing errors via `git stash`).
- **Phase gate:** `python -m pytest -q` green; `npm --prefix dashboard run build` succeeds; `trading-safety-reviewer`, `strategy-tuning-reviewer`, `test-isolation-reviewer`, `ui-consistency-reviewer` all run; manual look at the Strategy P&L tab on a rebuilt bundle.

### Wave 0 Gaps
- [ ] `tests/test_exit_recheck.py` — all rows above marked ❌ (use synthetic fixtures; no segment can drift on real data today)
- [ ] Feasibility spike for the India replay (Open Question 5): run on a copy/read-only handle of `memory/market_log.sqlite`; never write the real DBs
- [ ] Fixtures: tmp `trade_memory.sqlite` (already redirected by conftest), tmp `crypto.journal.JOURNAL_PATH`, tmp `commodities.lanes.JOURNAL_PATH`, `data_epoch` stub, tmp `market_log.DB_PATH` (conftest already does the last)
- Framework install: none needed.

## Security Domain

### Applicable ASVS Categories (level 1, `security_enforcement` true)

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (new) | Existing `DASHBOARD_PASSWORD` basic-auth middleware gates all `/api`; nothing new |
| V3 Session Management | no | — |
| V4 Access Control | yes (light) | New POST is non-financial (read-only compute + writes only its own baseline row) like `POST /api/settings/features`; it must NOT be able to change any stop value or send an order. Keep it out of `require_admin_secret` only because it cannot move money; state this in the plan |
| V5 Input Validation | yes | POST takes no body (or an enum `trigger`); validate; never interpolate request data into SQL (use `?` params as the repo does); server computes everything |
| V6 Cryptography | no | — |
| V7 Logging/Errors | yes | Do not leak exception text/paths to the client beyond the repo's existing `str(exc)[:200]` convention; no secrets in the Telegram text |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Re-check silently becoming an auto-tuner (writes a stop value) | Tampering | Module is read-only w.r.t. `SELL_TRAIL_POINTS`/`instruments`/`.env`; only writes its own `learned_settings` row; `strategy-tuning-reviewer` checks no path writes a parameter |
| Event-loop stall / DoS via repeated "Re-check now" clicks | Denial of Service | `asyncio.to_thread`, one module `threading.Lock` (second concurrent run returns the in-progress/last result), `Button pending` |
| Read-modify-write race corrupting baseline | Tampering | Single lock + absolute values (CLAUDE.md rule) + `ON CONFLICT` upsert |
| Real Telegram message from a test | Information disclosure | conftest clears token; monkeypatch `notify.alert`; `test-isolation-reviewer` |
| SQL injection via instrument/segment strings | Tampering | Parameterised queries only |
| Real journal/DB mutation by a test or by the replay spike | Tampering | Open `market_log.sqlite` read-only (`mode=ro` URI) in the spike; tests use `tmp_path` |

## Sources

### Primary (HIGH confidence — files read or queried this session)
- `index_ai/premium_trail.py`, `index_ai/trailing.py`, `index_ai/position_exits.py`, `index_ai/strategies/credit_spread.py`, `index_ai/instruments.py`, `index_ai/plan_builder.py`, `index_ai/strategies/premium_sell.py`, `strategy_params.py`, `strategy_router.py`
- `index_ai/exit.py`, `index_ai/learning.py` (1395-1428), `index_ai/day_review.py`, `index_ai/strategy_learning.py`, `index_ai/strategy_performance.py`, `index_ai/strategy_lab.py`, `index_ai/market_log.py`, `index_ai/daily_ops.py`, `index_ai/scanner.py` (402-600), `index_ai/server.py` (262-410, 1429-1610), `index_ai/notify.py`, `index_ai/reconcile.py`, `index_ai/atomic_io.py`, `index_ai/trade_lots.py`, `index_ai/cloud_backup.py`, `index_ai/data_epoch.py`
- `crypto/strategies/trailing.py`, `crypto/config.py`, `crypto/lanes.py` (168-199, 1195-1260), `crypto/journal.py`, `crypto/day_review.py`, `crypto/candle_cache.py`; `commodities/lanes.py`, `commodities/config.py`, `index_ai/strategies/futures/price_trail.py`
- `dashboard/src/components/pages/StrategyPerformancePage.tsx`, `FeaturesPanel.tsx`, `ui/Button.tsx`, `hooks/useStrategyLearning.ts`, `lib/api.ts`, `lib/theme.ts`, `dashboard/package.json`
- `tests/conftest.py`, `tests/test_position_exits.py`, `tests/test_premium_trail.py`, `tests/test_buy_scalp_trail.py`, `tests/test_strategy_learning.py`, `tests/test_reconcile_fault_injection.py`, `tests/test_admin_auth.py`
- `.claude/agents/strategy-tuning-reviewer.md`, `ui-consistency-reviewer.md`, `test-isolation-reviewer.md`; `CLAUDE.md`; `.planning/config.json`, `REQUIREMENTS.md`, `03-CONTEXT.md`
- Read-only queries this session: `memory/trade_memory.sqlite` (trades/feedback), `memory/market_log.sqlite` (ticks/chain/observations coverage), `memory/crypto_journal.jsonl`, `memory/commodity_journal.jsonl`, `memory/futures_journal.jsonl`, `memory/data_epoch.txt`
- Claude auto-memory notes: `strategy-analysis-and-simplification-directive.md`, `standard-trailing-stop-and-profit.md`, `strategy-findings.md`
- Test run: 91 passed in 9.87 s (list under Q8)

### Secondary (MEDIUM) / Tertiary (LOW)
- None. No web research was needed; no external library is involved.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all helpers located and read.
- Architecture: HIGH for deletion and hook/alert/persistence; MEDIUM for the India replay (feasibility proven at the data level, not yet exercised end-to-end — hence the Wave 0 spike).
- Pitfalls: HIGH — each is tied to a read line or a real row.

**Research date:** 2026-10-03
**Valid until:** 2026-10-17 (fast-moving: the stop numbers and journals change daily; re-run the segment counts before planning thresholds)
