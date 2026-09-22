"""P&L-based trailing stop / target for the crypto lanes.

All levels are **percent of P&L on the margin deployed**, not percent of price.
Because margin = notional / leverage,

    pnl_pct = (price / entry - 1) * direction * leverage * 100

so at the 20x default a stop at -16% P&L is a ~0.8% adverse *price* move — a real
swing stop. (At the old 100x default the same -10% stop was a 0.1% price wiggle,
which stopped every trade out on noise — 2026-09-11 backtest: 100x -$384/45d vs
20x -$272/45d on the 6 PM strategy.)

Rules (Richard, 2026-09-07; retuned 2026-09-11, retuned again 2026-09-22):
  * initial stop at ``-stop_pnl_pct`` (default -16% P&L ~ -0.8% price at 20x)
  * every ``ratchet_step_pnl_pct`` (5%) of peak P&L lifts the stop by the same
  * at ``tp_trigger_pnl_pct`` (25%) the trailing-profit floor engages
  * past that, the floor trails ``peak_trail_pnl_pct`` (4%) behind the peak

The 2026-09-22 retune (crypto/config.py has the measurement) replayed real
closed trades against a grid of candidate settings — the prior 24/10/45/6
defaults let too much of a real peak give back before the floor caught up
(e.g. a trade peaking at +18% not exiting until -18%).

``update_and_check`` mutates ``pos['peak_pnl_pct']`` / ``pos['trail_stop_pnl_pct']``
and returns an exit reason string when current P&L has fallen to the stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TrailConfig:
    leverage: float = 20.0
    stop_pnl_pct: float = 16.0
    ratchet_step_pnl_pct: float = 5.0
    tp_trigger_pnl_pct: float = 25.0
    peak_trail_pnl_pct: float = 4.0


def pnl_pct(entry: float, price: float, side: str, leverage: float) -> float:
    if entry <= 0:
        return 0.0
    direction = 1.0 if side == "long" else -1.0
    return (price / entry - 1.0) * direction * leverage * 100.0


def price_at_pnl(entry: float, pnl: float, side: str, leverage: float) -> float:
    """Inverse of ``pnl_pct`` — the price at which P&L would equal ``pnl``."""
    if leverage <= 0:
        return entry
    direction = 1.0 if side == "long" else -1.0
    return entry * (1.0 + pnl * direction / (100.0 * leverage))


def stop_level(peak: float, cfg: TrailConfig) -> float:
    """The P&L% at which the position should be closed, given the peak P&L% so far."""
    peak = max(0.0, peak)
    if peak < cfg.tp_trigger_pnl_pct - 1e-6:
        step = max(cfg.ratchet_step_pnl_pct, 1e-9)
        return -cfg.stop_pnl_pct + cfg.ratchet_step_pnl_pct * math.floor(peak / step + 1e-6)
    return max(cfg.tp_trigger_pnl_pct, peak - cfg.peak_trail_pnl_pct)


def update_and_check(
    pos: dict,
    price: float,
    cfg: TrailConfig,
    *,
    low: float | None = None,
    high: float | None = None,
) -> str | None:
    """Checks the stop/target against ``price`` — or, when ``low``/``high`` are
    given (the current candle's real range, not just one live tick), against
    the full range a 60s-ish poll could otherwise miss a touch inside of
    (2026-09-22: a scan every ~60s can still miss a spike-and-reverse that
    happens between two polls; the candle's own OHLC already recorded it).

    The adverse extreme is checked against the floor as it stood *before*
    this candle, on purpose — OHLC alone can't tell us whether the favorable
    or adverse extreme happened first, so this is the conservative reading:
    it never misses a real touch, though it may rarely close a trade the
    peak would have already cleared later in the same candle.

    Sets ``pos['trail_exit_price']`` to the realistic fill level when it
    returns an exit reason — the floor's price when the range caught it,
    otherwise ``price`` — so the caller books the exit where it would
    actually have filled, not wherever price happens to be now.
    """
    entry = float(pos["entry_price"])
    side = pos["side"]
    lev = cfg.leverage
    lo = float(low) if low is not None else float(price)
    hi = float(high) if high is not None else float(price)
    long = side == "long"
    favorable_price = hi if long else lo
    adverse_price = lo if long else hi

    peak_before = max(0.0, float(pos.get("peak_pnl_pct", 0.0)))
    stop_before = stop_level(peak_before, cfg)
    adverse_pct = pnl_pct(entry, adverse_price, side, lev)
    if adverse_pct <= stop_before:
        pos["peak_pnl_pct"] = round(peak_before, 2)
        pos["trail_stop_pnl_pct"] = round(stop_before, 2)
        pos["trail_exit_price"] = round(price_at_pnl(entry, stop_before, side, lev), 8)
        kind = "trailing profit" if stop_before >= 0 else "trailing stop"
        return f"{kind} {stop_before:+.0f}% P&L (peak {peak_before:+.0f}%, now {adverse_pct:+.0f}%)"

    favorable_pct = pnl_pct(entry, favorable_price, side, lev)
    peak = max(peak_before, favorable_pct, 0.0)
    pos["peak_pnl_pct"] = round(peak, 2)
    stop = stop_level(peak, cfg)
    pos["trail_stop_pnl_pct"] = round(stop, 2)
    cur = pnl_pct(entry, float(price), side, lev)
    if cur <= stop:
        pos["trail_exit_price"] = round(float(price), 8)
        kind = "trailing profit" if stop >= 0 else "trailing stop"
        return f"{kind} {stop:+.0f}% P&L (peak {peak:+.0f}%, now {cur:+.0f}%)"
    pos.pop("trail_exit_price", None)
    return None


def bracket_stop_price(entry: float, side: str, cfg: TrailConfig) -> float | None:
    """Absolute price for the exchange bracket stop — the -stop_pnl_pct level,
    a server-down backstop at the same place the engine's initial stop sits."""
    if entry <= 0 or cfg.leverage <= 0:
        return None
    frac = cfg.stop_pnl_pct / (100.0 * cfg.leverage)
    return round(entry * (1 - frac) if side == "long" else entry * (1 + frac), 2)


if __name__ == "__main__":  # self-check — walk a long trade through the whole path
    # pin the classic values so this tests the ratchet / floor math, not the
    # (retuned) module defaults
    cfg = TrailConfig(
        leverage=100.0,
        stop_pnl_pct=10.0,
        ratchet_step_pnl_pct=5.0,
        tp_trigger_pnl_pct=25.0,
        peak_trail_pnl_pct=2.0,
    )
    entry = 100.0
    pos = {"entry_price": entry, "side": "long"}

    def at(price_move_pct: float) -> str | None:
        return update_and_check(pos, entry * (1 + price_move_pct / 100.0), cfg)

    # +0.05% price = +5% P&L -> stop ratchets to -5%
    assert at(0.05) is None and pos["trail_stop_pnl_pct"] == -5.0
    # +0.10% = +10% P&L -> breakeven stop
    assert at(0.10) is None and pos["trail_stop_pnl_pct"] == 0.0
    # +0.15% = +15% -> stop +5%
    assert at(0.15) is None and pos["trail_stop_pnl_pct"] == 5.0
    # pull back to +12% P&L: still above the +5% stop -> hold
    assert at(0.12) is None
    # peak preserved (was 15%), drop to +4% -> below the +5% stop -> exit
    r = at(0.04)
    assert r and "trailing" in r and "+5%" in r, r

    # fresh trade: run to +40% P&L, floor trails to +38%
    pos2 = {"entry_price": entry, "side": "long"}
    update_and_check(pos2, entry * 1.004, cfg)  # +40% P&L
    assert pos2["trail_stop_pnl_pct"] == 38.0
    # +25-27% band holds the floor at 25
    pos3 = {"entry_price": entry, "side": "long"}
    update_and_check(pos3, entry * 1.0026, cfg)  # +26% P&L
    assert pos3["trail_stop_pnl_pct"] == 25.0

    # short trade: price down 0.15% = +15% P&L
    ps = {"entry_price": entry, "side": "short"}
    assert update_and_check(ps, entry * 0.9985, cfg) is None and ps["trail_stop_pnl_pct"] == 5.0

    assert bracket_stop_price(100.0, "long", cfg) == 99.9
    assert bracket_stop_price(100.0, "short", cfg) == 100.1

    # range check (leverage=100 here, so 0.01% price = 1% P&L): a candle that
    # spiked down through the stop and back up — a single "current price"
    # check would miss this entirely
    pos4 = {"entry_price": entry, "side": "long"}
    r4 = update_and_check(pos4, 100.3, cfg, low=99.8, high=100.5)  # low -20% P&L, stop is -10%
    assert r4 and "trailing stop" in r4, r4
    assert pos4["trail_exit_price"] == price_at_pnl(entry, -10.0, "long", cfg.leverage), pos4

    # range check: peak advances off the high even though price (the "now"
    # value) has already pulled back
    pos5 = {"entry_price": entry, "side": "long"}
    r5 = update_and_check(pos5, 100.05, cfg, low=100.0, high=100.12)  # high = +12% peak
    assert r5 is None and pos5["peak_pnl_pct"] == 12.0, (r5, pos5)

    print("crypto.strategies.trailing self-check ok")
