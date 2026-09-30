---
phase: 01-strategy-fixes
reviewed: 2026-09-30T12:41:37Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - dashboard/src/components/StrategyTuningPanel.tsx
  - guides/Strategy Guide.md
  - index_ai/strategies/buy_strategy.py
  - index_ai/strategies/options_cpr/viability.py
  - index_ai/strategies/strategy_params.py
  - index_ai/strategy_lab.py
  - index_ai/strategy_performance.py
  - tests/test_buy_strategy.py
  - tests/test_options_cpr.py
  - tests/test_strategy_lab.py
  - tests/test_strategy_performance.py
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 01-strategy-fixes: Code Review Report

**Reviewed:** 2026-09-30T12:41:37Z
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Reviewed the two new buy-lane gates (`BUY_BLOCK_CONTRA_CPR`, `BUY_BLOCK_INTO_OI_WALL`) in
`buy_strategy.py`, the buy-lane row removal in `viability.py`, the new `strategy_params.py`
switches and dashboard summary keys, the `since=` cutoff added to `strategy_performance.py`,
and the new real-option-chain replay candidates (`live_buy_lane` / `live_buy_lane_tuned`) plus
their look-ahead safeguards in `strategy_lab.py`.

**Money-path safety:** confirmed clean. None of the reviewed files import `executor`,
`dhan_orders`, or any order-placing path — `strategy_lab.py` only ever fills against recorded
historical quotes and computes charges, it places nothing. `buy_strategy.py` only returns
`StrategySignal` objects; arming/order logic lives elsewhere and is untouched here.

**Gate symmetry:** both new buy-lane gates are correctly mirrored between the bull (`BUY_CALL`)
and bear (`BUY_PUT`) branches — `buy_block_contra_cpr` blocks `{TRENDING_BEAR, SIDEWAYS}` for
bulls and `{TRENDING_BULL, SIDEWAYS}` for bears; `buy_block_into_oi_wall` checks
`walls[1] - price` (call wall) for bulls and `price - walls[0]` (put wall) for bears, with the
same `0 <=` guard against an already-broken wall on both sides. Both gates default off
(`False`), so the paper lane is provably unchanged until an operator flips the switch. Verified
against `tests/test_buy_strategy.py`'s dedicated bull/bear-mirror tests, and re-ran the full
targeted suite (`test_buy_strategy.py`, `test_options_cpr.py`, `test_strategy_lab.py`,
`test_strategy_performance.py` — 57 passed) plus `npx tsc --noEmit` on the dashboard (clean).

**Look-ahead bias in the `strategy_lab.py` replay adapter:** not found. `_live_buy_read` filters
every bar frame to `bars[bars["end"] <= pd.Timestamp(ts)]` before calling `evaluate_buy_signal`,
so the still-forming candle is never used (this is explicitly asserted by
`test_live_buy_read_no_look_ahead_and_builds_correct_inputs`). The OI context is built from the
current snapshot only, and `_trail_hit` walks real recorded index ticks strictly between the
last-seen cursor and the current snapshot time. Signal computation (`sigs[i]`) and its fill
(`_quotes(snap)`) both come from the same snapshot `i`, which models "decide now, fill at now's
quote" correctly rather than leaking a future price.

No blockers found. Two warnings and two info-level items below are worth addressing.

## Warnings

### WR-01: `strategy_scorecard(since=...)` is not reachable from anywhere the dashboard calls

**File:** `index_ai/strategy_performance.py:297` (consumed by `index_ai/server.py:1567-1573`)
**Issue:** The module docstring and `strategy_scorecard`'s own docstring describe `since` as
existing specifically "to judge a changed config (e.g. the tuned NIFTY buy entries) on its own
trades" — but `GET /api/strategy-performance` calls `strategy_scorecard()` with no arguments
(`index_ai/server.py:1573`, `return await asyncio.to_thread(strategy_scorecard)`), and no other
endpoint or dashboard component passes a `since` value. As shipped, the only way to exercise this
cutoff is a direct Python call (tests do this). If the intent for this phase was to let
Richard actually see "trades since the tuned buy switch" on the dashboard, that outcome doesn't
exist yet — the parameter is correct and well-tested in isolation, but the feature it was built
for isn't observable anywhere a user can reach.
**Fix:** Either wire a `since` query parameter through the endpoint (e.g.
`async def strategy_performance_api(since: str | None = Query(None))`) or, if this is
intentionally staged for a later phase/script-only use, say so in a comment at the call site so
a future reviewer doesn't assume it's already live.

### WR-02: `friction_floor(..., lane="buy")` computes 2 order-charges but is labeled `legs=1`

**File:** `index_ai/strategies/options_cpr/viability.py:108-112`
**Issue:** Not new to this phase, but now directly exercised by the buy-lane `UNMEASURED`
path added here (`viability("NIFTY", "buy")` still calls `friction_floor(cfg, lane="buy")`
under the hood via the `legs` field returned in `Viability.legs`). The buy branch charges both
a `BUY` and a `SELL` order (`leg_charge_rupees(prem, lot, "BUY", ...) + leg_charge_rupees(prem,
lot, "SELL", ...)`) — i.e. two executed orders (entry + exit) — but returns `legs=1`. Every sell
branch's `legs` count is `legs * 2` (order count), so the buy branch is inconsistent with its own
sibling: it reports "1" for what is, by the same convention used two lines below, "2" round-trip
orders. `Viability.to_dict()["legs"]` is shown in the daily-ops report
(`index_ai/daily_ops.py:285-290`, `f"{key} {lane}": ... "{legs} orders..."` in the `NOT_VIABLE`
reason string) so this now surfaces a wrong order count for the buy lane specifically once a
`gross_per_trade` override is supplied (the `UNMEASURED` path doesn't reach the reason string
that cites `legs`, but any future caller passing `gross_per_trade` for `"buy"` will get a
misleading `legs=1` in a verdict whose reason text says "on a normal book" referencing `legs`).
**Fix:** `return charges + near_hs * lot * 2, 2, src` (2 orders: buy-to-open, sell-to-close) to
match the sell branch's own order-counting convention, or rename the field to make clear it's
option-legs (always 1 for a buy) vs. executed orders (2) if that distinction is intentional.

## Info

### IN-01: `WALL_ROOM_PCT` reads as 10% but means 0.10%

**File:** `index_ai/strategies/buy_strategy.py:23`
**Issue:** `WALL_ROOM_PCT = 0.10` is correct and well-tested (confirmed against
`tests/test_options_cpr_...` — actually `tests/test_buy_strategy.py`'s wall-room tests, e.g.
"+15 pts < 24.09 (0.10% of 24090)"), but the name and the bare literal `0.10` invite a future
maintainer to misread it as "10% of price" rather than "0.10% of price" — the division by `100`
at each use site (`price * WALL_ROOM_PCT / 100`) is the only thing that makes it 0.10%, and nothing
in the constant's own name signals that a second `/100` is coming.
**Fix:** Rename to something self-documenting, e.g. `WALL_ROOM_PCT_OF_PRICE = 0.0010` and drop
the `/ 100` at each call site, or keep the current scale but rename to
`WALL_ROOM_BASIS_POINTS_TIMES_10` / add an inline `# 0.10%, i.e. 10 basis points` at the
constant itself instead of only in the module comment above it.

### IN-02: duplicate win-rate computation in `strategy_lab.run()`

**File:** `index_ai/strategy_lab.py:643-658`
**Issue:** `win = sum(t["net"] > 0 for t in mine) / n if n else 0.0` is computed once to decide
the `PASSING`/`DROPPED` verdict, then the identical expression
`sum(t["net"] > 0 for t in mine) / n` is recomputed a few lines later for the `"win_rate"`
output field instead of reusing `win`. Harmless (small `n`, no correctness issue), but it's a
straightforward DRY violation that could drift out of sync if one copy is edited later.
**Fix:** `"win_rate": round(win, 3) if n else None,`

---

_Reviewed: 2026-09-30T12:41:37Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
