# Phase 1: Strategy Fixes - Pattern Map

**Mapped:** 2026-09-30
**Files analyzed:** 4 (3 edited, 1 with one new addition)
**Analogs found:** 4 / 4

This phase is almost entirely parameter tuning inside files that already
implement the target pattern — there is no "new file needs an analog from a
different corner of the codebase" situation for the tuning tasks. The one
genuinely new code unit (a `strategy_lab.py` CANDIDATES entry that exercises
`evaluate_buy_signal` against real recorded option-chain data) has a direct,
concrete analog inside the same file.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `index_ai/strategies/strategy_params.py` | config | request-response (dataclass field read via `get_strategy_params()`) | itself — editing existing defaults, not a new pattern | exact |
| `index_ai/strategies/buy_strategy.py` | service (pure decision function) | request-response | itself — existing gates being tightened, no new gate shape needed | exact |
| `index_ai/strategies/breakout.py` | utility (pure function) | transform | itself — `confirm_bars` already parameterised, just needs a new default/value | exact |
| `index_ai/strategy_lab.py` (new CANDIDATES entry + `run_session`-compatible adapter) | service (backtest/replay adapter) | batch/replay (event-driven per recorded snapshot) | `index_ai/strategy_lab.py`'s own `pa_pullback_buy` / `oi_wall_bounce_buy` candidates (lines 141-150, `_pullback_read`/`_wall_bounce_read`, lines 279-307) | role-match (same file, same lane, different signal source — see Adapter section below) |

## Pattern Assignments

### `index_ai/strategies/strategy_params.py` (config)

**Analog:** itself — no external pattern needed, this is a dataclass field
value change.

**Current defaults to tune** (`strategy_params.py:15-119`, `[VERIFIED]` per
RESEARCH.md):
```python
require_supertrend_align: bool = True       # buy_strategy.py:138,158
breakout_lookback: int = 20                 # feeds detect_breakout's lookback
entry_confirmation_bars: int = 2            # feeds detect_breakout's confirm_bars
cpr_narrow_width_pct: float = 0.35          # CprRegime day_bias classification
cpr_wide_width_pct: float = 0.75            # -> "SIDEWAYS" veto at buy_strategy.py:120
```

**Load pattern** — `.env`-backed, cached, needs explicit reload:
```python
# strategy_params.py:122, :406-408 (paraphrased from RESEARCH.md, re-verify exact lines before editing)
@lru_cache(maxsize=1)
def get_strategy_params() -> StrategyParams: ...
def reload_strategy_params() -> StrategyParams: ...  # only way to pick up a changed value without restart
```
**Apply to:** any plan task that tunes a gate value must either (a) tell the
user the exact `.env` line to set and ask for a restart (`.env` is
permission-blocked per `CLAUDE.md`), or (b) call `reload_strategy_params()`
explicitly inside a backtest/test context — never assume a dataclass default
edit alone changes live behavior.

---

### `index_ai/strategies/buy_strategy.py` (service, request-response)

**Analog:** itself. The two gates this phase tunes are already fully wired;
no new gate shape is needed, only threshold/flag changes plus (optionally)
one new veto following the exact shape of the existing ones.

**Existing veto pattern to copy if a new gate is added** (`buy_strategy.py:137-146`):
```python
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
Mirror block exists for `direction == "bear"` (`buy_strategy.py:157-166`) —
any new gate must be added symmetrically to both branches, same
`entry_quality` tag convention (short lowercase_snake reason code), same
`**base_fields` splat.

**The SIDEWAYS/breakout veto** (`buy_strategy.py:120-127`) — the other
concrete precedent for "tighten a CPR-driven gate":
```python
if pattern in {"breakout_resistance", "breakdown_support"} and regime.day_bias == "SIDEWAYS":
    return StrategySignal(
        action="NO_TRADE",
        reason=f"{setup['reason']} — CPR reads SIDEWAYS, breakout skipped.",
        confidence=0.0,
        entry_quality="cpr_sideways_veto",
        **base_fields,
    )
```

**Do not touch:** `REQUIRE_BREAKOUT_TAG` / `strategy.intraday_strategy_signal`
— confirmed dead for the live buy lane (only reachable from the manual
`/api/analyze` debug endpoint). Tuning it changes nothing the scanner runs.

---

### `index_ai/strategies/breakout.py::detect_breakout` (utility, transform)

**Analog:** itself — `confirm_bars` parameter already implements the
fake-breakout-confirmation mechanism this phase is meant to tune, not
reinvent.

```python
# breakout.py:8-21 (signature + the exact comment documenting the last tuning pass)
def detect_breakout(candles: pd.DataFrame, *, lookback: int = 20, confirm_bars: int = 1) -> dict:
    """
    ...
    ``confirm_bars=1`` (the old, single-bar behaviour) is a plain close
    crossing the level — the classic setup for a fakeout... 2026-09-16: raised
    the default caller-side to require 2 confirmed closes after a bad day
    traced partly to single-bar breakout entries reversing straight into
    their stop.
    """
```
Precedent: `entry_confirmation_bars` was already raised once (1 → 2) on
2026-09-16 for exactly this reason. A further raise (e.g. to 3) is "more of
the same lever," not a new mechanism — follow this exact precedent rather
than inventing a different confirmation shape.

---

### `index_ai/strategy_lab.py` — new CANDIDATES entry wrapping `evaluate_buy_signal` (D-08 sanity check)

**Analog:** `pa_pullback_buy` / `oi_wall_bounce_buy`, the two existing "buy"
lane candidates (`strategy_lab.py:141-150`), plus their signal-reader
functions `_pullback_read` (lines 279-290) and `_wall_bounce_read` (lines
293-306).

**Why these are the closest match, not a new harness:** Both are already
`lane="buy"`, already scored against `BUY_MIN_WIN_RATE = 0.65`
(`strategy_lab.py:49` — matches Richard's bar exactly), already replay real
`market_log.chain` snapshots + real index ticks, already use the same
`BUY_TRAIL_POINTS` 1:1 index trailing stop the live buy lane uses
(`strategy_lab.py:44-47`, sourced from `instruments._buy_scalp_trail`). The
**only** structural difference for the new candidate is that its direction
rule needs more than a `dict[str, Any] -> int` read of `oi_signals.read()` —
it needs to call `evaluate_buy_signal(frame, previous_day, regime, params, oi)`
and translate `StrategySignal.action` into the `Direction` callable's `+1 /
-1 / 0` contract.

**CANDIDATES registration pattern to copy** (`strategy_lab.py:141-150`):
```python
CANDIDATES: dict[str, tuple[str, Direction, str]] = {
    ...
    "pa_pullback_buy": (
        "buy",
        _pullback,
        "Buy ATM option when today's 5m structure resumes after a one-candle dip",
    ),
    "oi_wall_bounce_buy": (
        "buy",
        _wall_bounce,
        "Buy ATM call when the index taps the biggest put-OI strike and closes back above (puts mirrored)",
    ),
}
```
A `live_buy_lane` (or similarly named) entry should follow the identical
3-tuple shape: `("buy", <new direction fn>, "<plain description>")`.

**Signal-reader pattern to copy** (`strategy_lab.py:293-306`, `_wall_bounce_read`):
```python
def _wall_bounce_read(snap: oi_signals.Snapshot, done: pd.DataFrame, sig: dict[str, Any]) -> int:
    """+1 when the last completed 5m candle dipped to the max put-OI strike
    (support) and closed back above it; -1 mirrored at the max call-OI
    strike (resistance). Not against a clear opposite structure."""
    if not len(done):
        return 0
    side = oi_signals._oi_by_side(snap)
    sup, res = oi_signals._wall(side["PE"]), oi_signals._wall(side["CE"])
    last = done.iloc[-1]
    if sup and last["low"] <= sup < last["close"] and sig.get("structure") != "DOWN":
        return 1
    if res and last["high"] >= res > last["close"] and sig.get("structure") != "UP":
        return -1
    return 0
```
The new reader (`_live_buy_lane_read` or similar) follows the same shape —
`(snap, done, sig) -> int` — but internally builds the four arguments
`evaluate_buy_signal` needs and maps its `StrategySignal.action`:
`BUY_CALL -> +1`, `BUY_PUT -> -1`, `NO_TRADE -> 0`. It is called from
`signals()` (`strategy_lab.py:324-338`) the same way `_pullback_read` and
`_wall_bounce_read` already are:
```python
# strategy_lab.py:335-336, the two existing calls to mirror
sig["pullback"] = _pullback_read(sig["structure"], done)
sig["wall_bounce"] = _wall_bounce_read(snap, done, sig)
```

**Where each `evaluate_buy_signal` argument comes from inside the lab replay
loop (resolves RESEARCH.md's Open Question 1 — not a blocker):**

| Argument | Source inside `strategy_lab.py`'s existing replay | Notes |
|---|---|---|
| `frame` (today's OHLC so far) | `_bars_5m(instrument, session)` already exists (`strategy_lab.py:267-276`), filtered to `bars["end"] <= ts` exactly as `signals()` already does for `done` (`strategy_lab.py:332`) | No new data source — reuse the existing `done` frame |
| `previous_day` (prior session OHLC) | `index_ai.candle_cache.load_cached_range(instrument, interval, from_date=prev_day, to_date=prev_day)` — confirmed present and cheap: `load_cached_range` reads per-day CSVs already written by the live scanner's ongoing candle-cache sync (`candle_cache.py:89-116`), independent of `market_log.chain`. `previous_day_cpr()` (`strategy.py:65`) only needs `high`/`low`/`close` of that frame, so this is a lightweight lookup, not a new pipeline. | **Caveat, not a blocker:** the cache only has a day if the live scanner (or a manual `sync_from_dhan` call) has run on/after it — verify each `market_log.chain` session (2026-09-23 onward) has its prior day cached before trusting the replay; skip/log-and-continue on any session where it's missing rather than raising |
| `regime` (`CprRegime`) | `cpr_regime.analyze_cpr_regime(today=frame, previous_day=previous_day, price=..., ema_fast=..., ema_slow=...)` (`cpr_regime.py:87-154`) — pure function, no I/O beyond the two frames above | Reads `get_strategy_params()` internally for `cpr_narrow_width_pct`/`cpr_wide_width_pct` — same global params object the live lane uses, so a tuning change is automatically reflected in the D-08 replay too |
| `oi` (`OptionOiContext`) | Build from the current `snap` (`oi_signals.Snapshot`, already loaded each loop iteration) — same option-chain rows `_wall_bounce_read` already reads via `oi_signals._oi_by_side(snap)` / `oi_signals._wall(...)` (`strategy_lab.py:299-300`); check `index_ai/options_oi.py:1-60`'s `OptionOiContext`/`oi_walls` constructor shape before wiring (not opened this pass — small, self-contained, low-risk to confirm at implementation time) |
| `params` | `get_strategy_params()` — the same global `StrategyParams` singleton every other caller in the codebase uses (`strategy_params.py:122`, `buy_strategy.py:37`) — do not build a lab-specific params object | Ensures the lab replay is testing the *actual* gate values, including any tuning this phase applies |

**Verdict/entry conventions to keep unchanged** (`strategy_lab.py:465-511`,
`run()`): `MIN_TRADES=30`, `MIN_DAYS=14`, `BUY_MIN_WIN_RATE=0.65`, net>0 —
already match Richard's bar exactly; the new candidate needs zero changes to
`run()`/`run_session()`'s fill, charge, or trailing-stop machinery, only a
new `Direction` function and its `CANDIDATES` registration.

## Shared Patterns

### Confidence ladder (STRAT-02) — read-only integration, no new code
**Source:** `index_ai/strategy_learning.py:34-39, 42-47, 62-64, 220-234`
**Apply to:** every plan task that ships a tuned gate value — must be routed
through `learning_report()`'s existing watching(<15)/observing(<40)/ready(40+)
states and the `_frozen()` check (never auto-apply to a currently
net-positive strategy). No new code belongs here per CONTEXT.md D-10; this is
a verification/routing pattern, not something to build.

### Per-pattern scorecard buckets — how to measure "fixed"
**Source:** `index_ai/strategy_performance.py:44-59` (quoted in RESEARCH.md;
re-verify exact lines at implementation time)
```python
if mode == "candlestick_buy":
    pattern = str(signal.get("entry_quality") or "").strip()
    if pattern:
        return f"{mode} · {pattern}"
```
**Apply to:** measuring D-06/D-07 (≥65% win rate, net-positive, 40+ trades)
— pull `strategy_scorecard()["india"]["rows"]` filtered to
`instrument == "NIFTY"` and `strategy.startswith("candlestick_buy")`, summed
across all pattern sub-buckets, not a single row.

### Env value helpers — if any new `.env` key is introduced
**Source:** `index_ai/env.py` (`env_bool`/`env_float`/`env_int`, used
throughout `strategy_params.py`)
**Apply to:** any new tunable value must go through these helpers rather than
a raw `os.getenv` call (ASVS V5 note from RESEARCH.md's Security Domain
section) — but note this phase is expected to only change *existing* key
defaults, not add new keys, per RESEARCH.md's Recommended Project Structure.

## No Analog Found

None — every file this phase touches already contains the pattern it needs;
the one new code unit (the lab candidate) has a same-file analog covering
both its registration shape and its signal-reader shape.

## Open Item Carried to Planner (not resolved by this pass)

`index_ai/options_oi.py`'s `OptionOiContext` constructor / `oi_walls()`
signature was **not opened** this pass (RESEARCH.md's Assumption A1 also
flagged this). It is small (per RESEARCH.md, lines 1-60) and low-risk, but
the planner or executor should read it before finalizing the adapter's `oi`
argument construction — everything else needed to size the adapter
(`previous_day` sourcing, `regime` computation) is now confirmed cheap and
already available via existing helpers, so this is the one remaining
concrete unknown, not a structural blocker.

## Metadata

**Analog search scope:** `index_ai/strategies/`, `index_ai/strategy_lab.py`,
`index_ai/candle_cache.py`, `index_ai/strategies/cpr_regime.py`,
`index_ai/strategy_performance.py`, `index_ai/strategy_learning.py`
**Files scanned:** 8 read in full or targeted sections this pass, plus
everything RESEARCH.md already verified (buy_strategy.py, breakout.py,
strategy_params.py, oi_credit.py, strategy_lab.py, cpr_regime.py's caller
strategy.py — see RESEARCH.md Sources)
**Pattern extraction date:** 2026-09-30
