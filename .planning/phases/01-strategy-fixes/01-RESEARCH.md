# Phase 1: Strategy Fixes - Research

**Researched:** 2026-09-30
**Domain:** Internal codebase archaeology — tuning an existing India-options buy-lane entry pipeline and confirming an existing confidence-ladder/backtest-tooling gap. No external libraries, no new dependencies.
**Confidence:** HIGH (every claim below is `[VERIFIED: path:lines]` from files opened this session, except where explicitly marked `[ASSUMED]`)

## Summary

Phase 1 tunes one real, already-partially-built pipeline
(`buy_strategy.evaluate_buy_signal` → `candlestick_patterns.detect_candlestick_setup`
→ `breakout.detect_breakout`, gated by `StrategyParams.require_supertrend_align`
and the CPR-width thresholds) and confirms two "verdict" systems (the
confidence ladder in `strategy_learning.py`, and Strategy Lab in
`strategy_lab.py`) either already satisfy their requirement or need a small,
well-scoped addition.

Three findings materially change what the planner should write, none of which
were visible from the phase description alone:

1. **The confidence ladder (STRAT-02) is fully implemented code, not a
   process rule** — `index_ai/strategy_learning.py` already computes
   watching/observing/ready state, the frozen-if-net-positive check, and
   per-(strategy, instrument) observations, reading the real journal filtered
   to `data_epoch()`. STRAT-02 needs zero new code, exactly as CONTEXT.md D-10
   asserts — the plan's job is to route this phase's changes through it and
   verify no shortcut occurred, not to build anything.

2. **`REQUIRE_SUPERTREND_ALIGN` and the CPR-gate levers named in the ML
   insight already gate the live buy lane** (`buy_strategy.py:138,158`,
   `cpr_narrow_width_pct`/`cpr_wide_width_pct` feed `CprRegime` used at
   `buy_strategy.py:120`) — but `REQUIRE_BREAKOUT_TAG`, also in
   `StrategyParams`, does **not** — it only gates a different, legacy signal
   path (`strategy.intraday_strategy_signal`, wired only to the manual
   `/api/analyze` debug endpoint, `server.py:1780-1794`) that the live scanner
   does not call. Tuning `REQUIRE_BREAKOUT_TAG` would touch nothing the buy
   lane actually runs — the planner should not propose it.

3. **No tool exists today that backtests the real live buy-lane logic
   (`evaluate_buy_signal`) against real recorded option-chain data.**
   `scripts/backtest_options_cpr.py` — the tool CONTEXT.md's D-08/D-09 and this
   phase's task framing both point to for the pre-paper sanity check — is
   explicitly a **Black-Scholes-proxy** backtest of a **different, retired**
   strategy (`options_cpr/`, legacy per `CLAUDE.md`) and never touches
   `market_log.chain`. `strategy_lab.py` does use real chain data
   (`market_log.chain`, live since 2026-09-23) but its own `CANDIDATES` dict
   is a separate, simpler rule set — none of its 10 candidates call
   `evaluate_buy_signal`, `detect_breakout`, or `detect_candlestick_setup`.
   The lazy, correct fix is a **new `strategy_lab` candidate** that wraps
   `evaluate_buy_signal` and reuses the lab's existing fill/charges/trailing-
   stop/verdict machinery — not a new standalone backtest script, and not
   `backtest_options_cpr.py`.

**Primary recommendation:** Tune the existing gates
(`REQUIRE_SUPERTREND_ALIGN`, `CPR_NARROW_WIDTH_PCT`/`CPR_WIDE_WIDTH_PCT`,
`ENTRY_CONFIRMATION_BARS`, `BREAKOUT_LOOKBACK`) via `.env`/`StrategyParams`
only; add one new `strategy_lab.py` candidate that calls `evaluate_buy_signal`
against `market_log.chain` sessions for the D-08 sanity check; let all
paper-trade evidence accumulate into the existing `strategy_learning.py`
ladder and `strategy_performance.strategy_scorecard()` (buy-lane rows are
already tagged `candlestick_buy · {pattern}`, filterable by
`instrument == "NIFTY"`); write no new confidence-ladder code.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Buy-lane entry signal (pattern + S/R + Supertrend gate) | API/Backend (strategy layer) | — | Pure decision function, no I/O; `index_ai/strategies/buy_strategy.py` |
| OI-wall support/resistance | API/Backend (strategy layer) | Database (chain snapshots) | Reads live chain via `OptionOiContext`, computed elsewhere and passed in |
| Fake-breakout confirmation | API/Backend (strategy layer) | — | Pure function over an OHLC frame, `breakout.detect_breakout` |
| Confidence ladder / suggestion gating | API/Backend (learning layer) | Database (SQLite journal) | Reads `learning.recent_trades()`, no execution coupling |
| Strategy Lab paper-trading + verdicts | API/Backend (research/proving-ground layer) | Database (`market_log.chain`, `ticks`) | Replays recorded quotes, never places orders |
| Backtest sanity check (D-08) | API/Backend (research tooling) | Database (`market_log.chain`) | Belongs inside `strategy_lab.py`, not a new script — see Finding 3 |
| Dashboard display of win rate / ladder state | Frontend Server (SSR/dashboard) | API/Backend (`/api/strategy-lab`, scorecard endpoints) | Out of scope this phase — no dashboard change requested |

## Standard Stack

Not applicable — this phase adds no new library or service. All work is
parameter tuning plus, at most, one new function in `strategy_lab.py` reusing
its existing `pandas`/`numpy`/sqlite3 stack (already a project dependency,
confirmed present via imports at `strategy_lab.py:32-38`).

## Package Legitimacy Audit

Not applicable — no external packages are installed or upgraded by this
phase.

## Architecture Patterns

### System Architecture Diagram

```
Live scanner (scanner.py, not modified this phase)
        │  builds today's OHLC frame + previous_day frame + OptionOiContext
        ▼
strategy_router.evaluate_dual_opportunities()   [strategy_router.py:102-138]
        │  style in {AUTO, BUY} → calls evaluate_buy_signal(frame, previous_day, regime, params, oi)
        ▼
buy_strategy.evaluate_buy_signal()              [buy_strategy.py:20-183]  <- THIS PHASE TUNES HERE
        │  walls = oi_walls(oi)                  [options_oi.py:28-38]
        │  setup = detect_candlestick_setup(...) [candlestick_patterns.py:29-154]
        │        │  sr = swing_levels(oi_support, oi_resistance)  -- OI walls win when present
        │        │  br = detect_breakout(frame, lookback, confirm_bars) [breakout.py:8-54]
        │        │  pattern match: engulfing / hammer / shooting_star / breakout_* / trend_pullback_*
        │  st = supertrend_snapshot(...)
        │  gate: pattern in {breakout_resistance, breakdown_support} + CPR SIDEWAYS → veto [buy_strategy.py:120]
        │  gate: require_supertrend_align + direction mismatch → veto [buy_strategy.py:138, 158]
        ▼
StrategySignal(action=BUY_CALL/BUY_PUT/NO_TRADE, entry_quality=pattern, ...)
        │
        ▼ (live path)                              ▼ (D-08 sanity-check path — TO BE ADDED)
executor.execute_plan() → dhan_orders             strategy_lab.run_session("buy_evaluate_signal", ...)
        │  writes to learning SQLite                    │  replays market_log.chain sessions,
        ▼                                                │  reuses _fill/_open/_trail_hit/charges
strategy_performance.strategy_scorecard()                ▼
        │  buckets by (strategy_mode·pattern, instrument)  strategy_lab verdict COLLECTING/PASSING/DROPPED
        ▼
strategy_learning.learning_report()  <- confidence ladder (watching/observing/ready/frozen), already built
```

### Recommended Project Structure

No new files/folders needed beyond one new function (and one new `CANDIDATES`
entry) inside the existing `index_ai/strategy_lab.py`. All tuning is `.env`
value changes read by `StrategyParams` (`index_ai/strategies/strategy_params.py`).

### Pattern 1: Parameter tuning is global, not per-instrument

**What:** `StrategyParams` is a single `@lru_cache(maxsize=1)`-loaded dataclass
(`strategy_params.py:122-194`), read via one module-level `get_strategy_params()`
call. Every caller across the codebase (`buy_strategy.py:37`,
`strategy_router.py:116`, `sell_strategy.py:82`, etc. — 15+ call sites found
via grep) uses the same global instance. There is **no per-instrument override
mechanism** for `StrategyParams` (unlike `options_cpr/config.py`'s
`config_for(instrument)` + `with_overrides()`, which is backtest-only tooling
per `CLAUDE.md` and irrelevant to the live buy lane).
`[VERIFIED: strategy_params.py:122-194, buy_strategy.py:37]`

**When to use:** Any `.env` change to `REQUIRE_SUPERTREND_ALIGN`,
`ENTRY_CONFIRMATION_BARS`, `BREAKOUT_LOOKBACK`, `CPR_NARROW_WIDTH_PCT`, or
`CPR_WIDE_WIDTH_PCT` applies to **NIFTY, BANKNIFTY, and SENSEX simultaneously**
— there is no code-level way to scope a change to NIFTY only.

**Reconciling with CONTEXT.md D-02 ("focus tuning on NIFTY first... prove it
works there before checking BankNifty/Sensex hold up on the same change"):**
this is not a contradiction — D-02's own wording ("the same change") already
assumes one global change is measured first against NIFTY's live trades, then
checked against the other two indices' live trades. **No per-instrument config
plumbing needs to be built this phase** — the scoping is in the *measurement*
(filter `strategy_scorecard()` rows to `instrument == "NIFTY"` first), not in
the parameter storage. Flag this explicitly to the planner so no one proposes
building instrument-scoped config as a task.

### Pattern 2: Buy-lane entries are scored per exact pattern, not lumped

**What:** `strategy_performance.py:44-59` (`_strategy_label` or equivalent —
read at lines 44-59) splits `candlestick_buy` trades into
`candlestick_buy · {entry_quality}` buckets (e.g. `candlestick_buy ·
bullish_engulfing`, `candlestick_buy · breakout_resistance`) — done 2026-09-16
"after a bad day traced to one specific pattern type."
`[VERIFIED: strategy_performance.py:44-59]` — quote: `"The buy lane's
``candlestick_buy`` mode covers six different patterns (engulfing, hammer,
breakout, trend-pullback, …) lumped into one bucket — 2026-09-16, after a bad
day traced to one specific pattern type, split it"` and `if mode ==
"candlestick_buy": pattern = str(signal.get("entry_quality") or "").strip();
if pattern: return f"{mode} · {pattern}"` (lines 56-59).

**When to use:** When measuring the phase's success criteria ("buy lane's
measured live win rate"), pull `strategy_scorecard()["india"]["rows"]` filtered
to `instrument == "NIFTY"` and `strategy.startswith("candlestick_buy")`,
summed across all pattern sub-buckets — not one single row.

### Pattern 3: The confidence ladder already exists, end to end

**What:** `index_ai/strategy_learning.py` implements exactly the ladder
CONTEXT.md/STRAT-02 describe:
```python
# strategy_learning.py:34-39 [VERIFIED]
WATCH_MAX = 15    # below this: collect only
OBSERVE_MAX = 40  # below this: observe, no suggestions
READY_MIN_DAYS = 15
FREEZE_LOOKBACK = 20  # net-positive over the last N recent trades → frozen
```
`_state(n_trades, n_days)` returns `"watching"` / `"observing"` / `"ready"`
(lines 42-47); `_frozen(pnls_recent)` returns `True` when
`len(pnls_recent) >= 5 and sum(pnls_recent) > 0` (lines 62-64);
`learning_report()` groups by `(strategy, instrument)` for both India and
crypto (lines 220-234) and is exercised by a `__main__` self-check (lines
237-269) and `tests/test_strategy_learning.py`
`[VERIFIED: file exists via Glob]`.

**When to use:** This phase's plan should **read** this ladder's output
(`learning_report()`, or the dashboard surface backed by it) to decide when a
NIFTY buy-lane parameter suggestion is allowed to move from
observe-only to human-approved-suggestion — never bypass it. No new code
belongs here per STRAT-02/D-10.

### Anti-Patterns to Avoid

- **Don't tune `REQUIRE_BREAKOUT_TAG`** — it only gates
  `strategy.intraday_strategy_signal` (`strategy.py:330`), reachable only from
  the manual `POST /api/analyze` debug endpoint (`server.py:1780-1794`), not
  from the live scanner → `strategy_router` → `buy_strategy` path this phase
  targets. `[VERIFIED: strategy.py:330, server.py:1792-1794]`
- **Don't reach for `scripts/backtest_options_cpr.py` for the D-08 sanity
  check** — its own module docstring states: `"Spot-replay with a
  Black-Scholes premium proxy (no historical option chain) — signal stats are
  meaningful, rupee P&L is indicative."` `[VERIFIED: backtest_options_cpr.py:9-10]`.
  It also runs a structurally different strategy
  (`options_cpr.backtest.run`, CPR+EMA legacy path — `backtest_options_cpr.py:94`),
  not `evaluate_buy_signal`. Using it would validate the wrong code entirely.
- **Don't build a new standalone backtest script for D-08** — `strategy_lab.py`
  already has every piece needed (real-chain snapshot loading via
  `oi_signals.load_session`, buy/sell fills at real bid/ask, real Dhan charges
  via `charges.leg_charge_rupees`, the 1:1 index trailing stop, and a
  COLLECTING/PASSING/DROPPED verdict machine) — adding one `CANDIDATES` entry
  is materially less code than a parallel harness (see Don't Hand-Roll below).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Real-chain backtest of the buy-lane logic (D-08) | A new `scripts/backtest_buy_lane.py` reading `market_log.chain` directly | A new candidate function inside `strategy_lab.CANDIDATES`, called from the existing `run_session()`/`run()` | `strategy_lab.py` already has session loading, real bid/ask fills, real charges, the 1:1 trailing stop, and verdict thresholds (`MIN_TRADES=30`, `MIN_DAYS=14`, `BUY_MIN_WIN_RATE=0.65` — matches Richard's ≥65% bar exactly, `strategy_lab.py:48-49`). Duplicating this in a new script is the exact "reinventing a few files over" case. |
| Confidence-ladder gating (STRAT-02) | A new approval-workflow module | `strategy_learning.py`'s existing `_state`/`_frozen`/`learning_report()` | Already built, tested, and reads the real journal filtered to `data_epoch()`. |
| Measuring "the buy lane's win rate" | A new ad-hoc SQL query over the trades table | `strategy_performance.strategy_scorecard()` (backs the dashboard's "Strategy P&L" tab, PR #50 per `CLAUDE.md`) | Already computes gross/charges/slippage/net per (strategy, instrument, mode) with real Dhan charges, and already splits `candlestick_buy` by pattern. |

**Key insight:** Every mechanism this phase needs — the entry logic, the OI
walls, the fake-breakout confirmation, the confidence ladder, and the
per-(strategy, instrument) scorecard — already exists in the codebase in a
finished or near-finished state. The only genuinely new code this phase should
produce is a small adapter: one `strategy_lab.py` candidate function that
converts a `market_log.chain` snapshot into the `(frame, previous_day, regime,
oi)` shape `evaluate_buy_signal` expects (see Open Questions — sizing this
adapter is the one piece of real design work left for planning).

## Common Pitfalls

### Pitfall 1: Assuming Strategy Lab verdicts predate the 2026-09-23 cutover

**What goes wrong:** STRAT-03 says "Strategy Lab verdicts (COLLECTING/
PASSING/DROPPED) are re-validated against the real recorded option-chain data
available since 2026-09-23... anything scored before that date used the
Black-Scholes proxy." A plan could naively create a task to "re-run old
Strategy Lab verdicts against real data."

**Why it happens:** The phrase sounds generic, but `strategy_lab.py` and the
`market_log.chain` table it depends on were **both created on 2026-09-23**
(`git log` on both files shows their earliest commit — `0bc808e` and
`d32431d` — dated 2026-09-23) `[VERIFIED: git log --format='%h %ad %s'
--date=short -- index_ai/strategy_lab.py and index_ai/market_log.py]`. Its
docstring states it has always used real quotes: `"paper-trade candidate
option strategies side by side on the real recorded option chain
(``market_log.chain``)"` (`strategy_lab.py:2-3`). There is **no pre-cutover
version of a Strategy Lab verdict** — the `GET /api/strategy-lab` endpoint
(`server.py:1529-1542`) recomputes every verdict live, each call, from
whatever sessions exist in `chain` (which only has rows from 2026-09-23
onward by construction).

**How to avoid:** The task that actually matches STRAT-03's spirit is
**`index_ai/strategies/options_cpr/viability.py`**, whose own module docstring
admits its **buy-lane** numbers are still the stale pre-cutover estimate:
`"Buy lane: still the 2026-08-29 backtest — live buy volume is under 30
trades/index, too thin to re-measure. Re-run the script and update the sell
rows once a lane clears ~30 forward trades."` (`viability.py:49-51`), and its
`OBSERVED_GROSS_PER_TRADE` dict literally hardcodes
`("NIFTY", "buy"): -51.0, ("BANKNIFTY", "buy"): -197.0, ("SENSEX", "buy"):
-80.0` (`viability.py:56-58`) computed before the BS-proxy-to-real-chain fix.
This is a different verdict vocabulary (`VIABLE`/`MARGINAL`/`NOT_VIABLE`/
`UNMEASURED`, not `COLLECTING`/`PASSING`/`DROPPED`) but is the actual
pre-cutover artifact CONCERNS.md means when it says `"Strategy viability
scores (options_cpr/viability.py) also predate this fix and should be re-run"`
`[VERIFIED: .planning/codebase/CONCERNS.md:28]`.

**Warning signs:** A plan task phrased as "audit Strategy Lab's historical
verdicts" with no target file will likely find nothing to fix (the lab has no
history to audit) and either silently no-op or drift into scope creep. Word
the task around the concrete file (`viability.py`'s `OBSERVED_GROSS_PER_TRADE`
buy-lane rows) or around explicitly documenting/flagging in the Strategy Lab
API response that "no verdict here predates the 2026-09-23 chain-recording
start" so a future reader doesn't need to re-derive this.

### Pitfall 2: Conflating `strategy_lab.py`'s CANDIDATES with the live buy lane

**What goes wrong:** Assuming that because `strategy_lab.py` already has
`pa_pullback_buy` and `oi_wall_bounce_buy` "buy" candidates
(`strategy_lab.py:141-150`), tuning those candidates is the same as tuning
`evaluate_buy_signal`.

**Why it happens:** Both are "OI + price-action buy logic," and both exist in
the same phase's problem space, but they are two **separately maintained**
rule sets — the lab candidates were purpose-built (2026-09-24,
`ce6bcb9`) as *alternative* strategies to test against the live one, not as a
harness for the live one. Confirmed via grep: `evaluate_buy_signal`,
`detect_breakout`, and `detect_candlestick_setup` are called only from
`strategy_router.py` and test files — never from `strategy_lab.py`
`[VERIFIED: Grep for these three names across *.py, 10 files, none in
strategy_lab.py]`.

**How to avoid:** Tuning `pa_pullback_buy`/`oi_wall_bounce_buy` does not fix
the live buy lane's win rate and is out of this phase's scope (D-01 pins the
lever to "the OI-wall support/resistance + fake-breakout confirmation work
already started... `buy_strategy.py`, `breakout.py`"). Keep these two systems
conceptually separate in the plan: existing lab candidates are informative
context (they show, e.g., that a pullback-based buy already tests ≥65% as a
verdict bar) but are not the thing being edited.

### Pitfall 3: Editing `StrategyParams` defaults without a `.env` restart

**What goes wrong:** `get_strategy_params()` is `@lru_cache(maxsize=1)`
(`strategy_params.py:122`) and reads `.env` via `load_dotenv(ENV_PATH,
override=False)` only inside that cached call — changing `.env` while the
server is running has no effect until `reload_strategy_params()` is called or
the process restarts (`strategy_params.py:406-408`).

**How to avoid:** Per `CLAUDE.md`, `.env` is permission-blocked in this
session and the server must be restarted after any `.env` change — any plan
task that tunes `REQUIRE_SUPERTREND_ALIGN`/`ENTRY_CONFIRMATION_BARS`/etc. must
either (a) tell the user the exact `.env` line to set and ask them to restart,
or (b) call `reload_strategy_params()` if the change is applied
programmatically in a test/backtest context. Do not assume a live value
changed just because a task edited a default in the dataclass.

## Code Examples

### Reading the current buy-lane gate values (no code change, just confirms live defaults)

```python
# Source: index_ai/strategies/strategy_params.py:15-119 (StrategyParams dataclass, VERIFIED)
require_supertrend_align: bool = True       # buy_strategy.py:138,158 — ML insight target #1
breakout_lookback: int = 20                 # feeds detect_breakout's `lookback`
entry_confirmation_bars: int = 2            # feeds detect_breakout's `confirm_bars` (fake-breakout gate)
cpr_narrow_width_pct: float = 0.35          # CprRegime day_bias classification
cpr_wide_width_pct: float = 0.75            # → "SIDEWAYS" veto at buy_strategy.py:120
buy_min_volume_ratio: float = 0.85
buy_volume_lookback_bars: int = 20
ml_gate_buy_min: float = 0.50               # separate from require_supertrend_align — ML score floor
```

### The existing veto this phase's ML insight is really about

```python
# Source: index_ai/strategies/buy_strategy.py:137-146 (VERIFIED)
if direction == "bull":
    if cfg.require_supertrend_align and st.get("ready") and st["direction"] != 1:
        return StrategySignal(
            action="NO_TRADE",
            reason=f"{setup['reason']} — Supertrend bearish, long skipped.",
            confidence=0.0,
            entry_quality="st_filter",
            ema_spread_pct=0.0,
            **base_fields,
        )
```
This is the exact lever the ML insight ("OI-aligned setups only win 33% —
consider tightening REQUIRE_SUPERTREND_ALIGN or CPR gates",
`index_ai/oi_learning.py:131`) names — it is already wired and already
defaults on (`True`). "Tightening" therefore means either (a) confirming it
stays on and instead tightening `cpr_narrow_width_pct`/`cpr_wide_width_pct` so
more days classify as SIDEWAYS (vetoing more breakout entries at
`buy_strategy.py:120`), or (b) raising `entry_confirmation_bars` beyond 2 so
`detect_breakout` demands more confirmed closes. Exact new values are
Claude's Discretion per CONTEXT.md — this research surfaces the levers and
their current defaults, not the target numbers.

### Existing verdict thresholds to reuse for the D-08 lab candidate (already match Richard's bar)

```python
# Source: index_ai/strategy_lab.py:48-49 (VERIFIED)
MIN_TRADES, MIN_DAYS = 30, 14
BUY_MIN_WIN_RATE = 0.65   # Richard's bar for option buying, on top of net > 0
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| BS-proxy backtest (`backtest_options_cpr.py`) for signal validation | Real-chain replay in `strategy_lab.py` against `market_log.chain` | 2026-09-23 | The old tool is still present and still runnable but produces "indicative," not trustworthy, rupee numbers (its own docstring says so) — don't reach for it for this phase's D-08 check |
| Single-bar breakout confirmation | `entry_confirmation_bars=2` (2+ consecutive confirmed closes) | 2026-09-16 (per `breakout.py:19-21` comment) | Already raised once after a bad day traced to single-bar entries reversing into their stop — a further raise is one of the available levers, not a novel idea |
| `candlestick_buy` scored as one bucket | Scored per exact pattern (`candlestick_buy · {pattern}`) | 2026-09-16 | Lets this phase's measurement isolate which pattern(s) are dragging NIFTY's win rate, not just the lane as a whole |

**Deprecated/outdated:**
- `REQUIRE_BREAKOUT_TAG` / `strategy.intraday_strategy_signal`: superseded by
  `strategy_router.evaluate_dual_opportunities` → `buy_strategy.evaluate_buy_signal`
  for live trading; only reachable via the manual `/api/analyze` debug
  endpoint now. Not part of this phase's tuning surface.
- `options_cpr/` backtest path (`backtest_options_cpr.py`, `options_cpr/backtest.py`):
  backtest-only per `CLAUDE.md`, uses BS-proxy pricing, tests a retired
  CPR+EMA strategy — not the live buy lane.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Building the D-08 sanity-check candidate as a `strategy_lab.py` addition (rather than a new script) is the lowest-effort correct path, and its adapter work (chain snapshot → `OptionOiContext`, previous-day frame lookup, `CprRegime` computation) is feasible reusing `oi_signals`/`candle_cache` helpers | Don't Hand-Roll, Open Questions | If the adapter turns out to need substantially more plumbing than expected (e.g., `previous_day` OHLC isn't cheaply available for `market_log.chain` session dates), the planner should size this as its own task/checkpoint rather than assuming it's trivial — flagged as an Open Question below, not asserted as fact |
| A2 | `[ASSUMED]` `candle_cache` (or an equivalent 1m/5m spot-history source) has previous-trading-day OHLC available for every session present in `market_log.chain` (2026-09-23 onward) — not independently confirmed this session, only that `candle_cache.load_cached_range` exists and is imported by `backtest_options_cpr.py:93` | Open Questions | If prior-day candles aren't cached for a `chain` session, `previous_day_cpr()` (used by `evaluate_buy_signal`) can't compute CPR pivots for that session and the D-08 replay would need to skip or approximate those days |

## Open Questions

1. **Exact shape of the `strategy_lab.py` adapter for `evaluate_buy_signal`**
   - What we know: `strategy_lab.py` already builds 5m OHLC bars from real
     index ticks (`_bars_5m`, `strategy_lab.py:267-276`) and has an OI-wall
     helper (`oi_signals._oi_by_side`/`_wall`, used at `strategy_lab.py:299-300`)
     that could feed an `OptionOiContext`-shaped support/resistance pair.
   - What's unclear: Whether `previous_day` OHLC (needed for
     `previous_day_cpr()` inside `evaluate_buy_signal`) is readily available
     for `market_log.chain` sessions via the existing candle cache, and
     whether `CprRegime` (`analyze_cpr_regime`) can be computed cheaply inside
     a lab replay loop without re-deriving indicators per snapshot.
   - Recommendation: Size this as an explicit, separately-verifiable planning
     task (not folded silently into "add a lab candidate") — the planner
     should read `index_ai/candle_cache.py` and `index_ai/strategies/cpr_regime.py`
     before committing to exact adapter code, since this research pass did not
     open those two files.

2. **Exact new parameter values for the tightened gates**
   - What we know: The levers are `require_supertrend_align` (already `True`),
     `entry_confirmation_bars` (currently 2), `cpr_narrow_width_pct`/
     `cpr_wide_width_pct` (currently 0.35/0.75), and `breakout_lookback`
     (currently 20).
   - What's unclear: What values raise NIFTY's win rate toward 65% without
     starving the lane of trades entirely — CONTEXT.md explicitly leaves this
     to "Claude's Discretion" during research + planning, and this phase's own
     D-08 backtest (once built) is the mechanism meant to answer it
     empirically, not a value this research should guess at.
   - Recommendation: Plan a "backtest sweep, pick values, paper-validate"
     sequence rather than hardcoding a specific number in the plan itself.

## Environment Availability

Skipped — this phase has no external tool/service dependency beyond the
already-running Dhan connection and SQLite (`market_log.py`), both already
verified present and in continuous use elsewhere in this codebase
(`market_log.enabled()` defaults `True`, `market_log.py:52-53`).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (`pyproject.toml:47-48`, `testpaths = ["tests"]`) `[VERIFIED: pyproject.toml:47-48]` |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_learning.py tests/test_strategy_lab.py -q` |
| Full suite command | `python -m pytest -q` (660 tests, ~7-8 min per `CLAUDE.md`) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STRAT-01 | Tightened gate still returns correct `StrategySignal` shape for known fixtures | unit | `pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py -x` | ✅ (3 files exist, 11 tests total, confirmed via AST parse this session) |
| STRAT-01 | NIFTY buy-lane live win rate reported correctly from scorecard after tuning | manual / dashboard read | n/a — reads `strategy_scorecard()` via `/api/strategy-performance`-style endpoint `[ASSUMED endpoint name — not confirmed this session]` | manual-only: real trading days must elapse |
| STRAT-02 | A parameter change only ships through watching→observing→ready | unit (existing) | `pytest tests/test_strategy_learning.py -x` | ✅ |
| STRAT-03 | Strategy Lab / viability verdicts correctly flagged pre/post cutover | unit (existing) + new | `pytest tests/test_strategy_lab.py -x` (existing); new candidate needs its own test | ✅ existing / ❌ Wave 0 for the new candidate |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_lab.py tests/test_strategy_learning.py -q`
- **Per wave merge:** `python -m pytest -q` (full suite, per `CLAUDE.md`)
- **Phase gate:** Full suite green before `/gsd-verify-work`; `ruff check index_ai/` compared against `git stash` baseline (per `CLAUDE.md`, ~8 pre-existing cosmetic errors expected)

### Wave 0 Gaps
- [ ] A test for the new `strategy_lab.py` candidate that wraps
  `evaluate_buy_signal` — covers STRAT-01's D-08 sanity check and STRAT-03
- [ ] No fixture currently exists asserting `viability.py`'s buy-lane
  `OBSERVED_GROSS_PER_TRADE` values carry a "stale/pre-cutover" flag once that
  re-validation lands — needed if the plan chooses to flag rather than re-run
  those numbers (STRAT-03)

*(Everything else — the existing gate logic, the confidence ladder, the
scorecard split — already has test coverage per the table above.)*

## Security Domain

`security_enforcement: true`, `security_asvs_level: 1` per `.planning/config.json:47-48`.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Phase touches no auth surface; existing `DASHBOARD_PASSWORD`/HTTP Basic gate (per `CLAUDE.md`) is unchanged |
| V3 Session Management | No | No session-related code touched |
| V4 Access Control | No | No new endpoint; if a new `/api/strategy-lab`-adjacent read is added it inherits the existing gate |
| V5 Input Validation | Marginal | Any new `.env` parameter value (if one is added rather than reusing existing keys) should go through the same `env_bool`/`env_float`/`env_int` helpers already used throughout `strategy_params.py` (`index_ai/env.py`) rather than a raw `os.getenv` |
| V6 Cryptography | No | No credential/crypto surface touched |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| A tuning task accidentally arms live orders while iterating | Elevation of Privilege | D-05 (CONTEXT.md) already requires paper-only throughout; verify no plan task touches `TRADING_MODE`/`ALLOW_LIVE_TRADING`/`ARM LIVE ORDERS` per `CLAUDE.md`'s two-lock rule |
| A new `strategy_lab` candidate accidentally places a real order | Tampering | `strategy_lab.py`'s own docstring guarantees "Nothing here places an order" (`strategy_lab.py:16`) — any new candidate must stay inside `run_session`'s pure-replay pattern, never call `executor`/`dhan_orders` |

## Sources

### Primary (HIGH confidence — files opened and read this session)
- `index_ai/strategies/buy_strategy.py` (full file, 184 lines)
- `index_ai/strategies/breakout.py` (full file, 55 lines)
- `index_ai/strategies/candlestick_patterns.py` (full file, 155 lines)
- `index_ai/strategies/strategy_params.py` (full file, 409 lines)
- `index_ai/strategies/oi_credit.py` (full file, 150 lines)
- `index_ai/oi_learning.py` (full file, 186 lines — source of the ML insight quote)
- `index_ai/strategy_learning.py` (full file, 270 lines — confidence ladder)
- `index_ai/strategy_lab.py` (full file, 512 lines)
- `index_ai/market_log.py` (lines 1-160, 249-276 — chain table + connect)
- `index_ai/server.py` (lines 1510-1552, 1770-1800 — `/api/strategy-lab`, `/api/analyze`)
- `index_ai/strategies/options_cpr/viability.py` (full file, 231 lines)
- `scripts/backtest_options_cpr.py` (lines 1-100)
- `index_ai/strategies/strategy_router.py` (lines 100-150)
- `index_ai/strategies/strategy.py` (lines 290-340 — `intraday_strategy_signal`, `REQUIRE_BREAKOUT_TAG`)
- `index_ai/strategy_performance.py` (lines 44-59, 280-350 — scorecard, crypto readiness)
- `index_ai/data_epoch.py` (full file, 58 lines)
- `index_ai/instruments.py` (lines 66-76, 150-160 — `market_lot_size`, `configured_index_keys`)
- `index_ai/options_oi.py` (lines 1-60 — `OptionOiContext`, `oi_walls`)
- `.planning/codebase/CONCERNS.md` (lines 25-29, 121-124, 163-167)
- `.planning/config.json` (full file — workflow toggles)
- `git log` on `index_ai/strategy_lab.py`, `index_ai/market_log.py`,
  `index_ai/strategies/options_cpr/viability.py` (dated commit history,
  confirms the 2026-09-23 cutover boundary)
- `pyproject.toml` (lines 47-48 — pytest config)
- AST-parsed `tests/test_buy_strategy.py`, `tests/test_breakout.py`,
  `tests/test_candlestick_patterns.py` (confirmed test function names)

### Secondary (MEDIUM confidence)
- None — no web/docs lookups performed; this phase is 100% internal codebase
  archaeology per the task framing, so no external `research-plan` seam calls
  were made.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: N/A — no external stack introduced
- Architecture: HIGH — every call path traced by reading the actual source, not inferred
- Pitfalls: HIGH — each pitfall grounded in a specific file:line contradiction found this session (dates, docstrings, grep results)

**Research date:** 2026-09-30
**Valid until:** Short shelf life — 7-14 days. This research is tied to exact
current defaults in `strategy_params.py` and the exact state of
`strategy_lab.py`'s `CANDIDATES`; both are expected to change as this very
phase executes. Re-verify defaults before acting on this document if more
than ~2 weeks have passed or if another phase/PR has touched these files.
