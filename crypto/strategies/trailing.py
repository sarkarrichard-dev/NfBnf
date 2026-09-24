"""P&L-based trailing stop / target for the crypto lanes.

All levels are **percent of P&L on the margin deployed**, not percent of price.
Because margin = notional / leverage,

    pnl_pct = (price / entry - 1) * direction * leverage * 100

so at the 20x default a stop at -24% P&L is a ~1.2% adverse *price* move — a real
swing stop. (At the old 100x default the same -10% stop was a 0.1% price wiggle,
which stopped every trade out on noise — 2026-09-11 backtest: 100x -$384/45d vs
20x -$272/45d on the 6 PM strategy.)

Rules (Richard, 2026-09-07; retuned 2026-09-11):
  * initial stop at ``-stop_pnl_pct`` (default -24% P&L ~ -1.2% price at 20x)
  * every ``ratchet_step_pnl_pct`` (10%) of peak P&L lifts the stop by the same
  * at ``tp_trigger_pnl_pct`` (45%) the trailing-profit floor engages
  * past that, the floor trails ``peak_trail_pnl_pct`` (6%) behind the peak

``update_and_check`` mutates ``pos['peak_pnl_pct']`` / ``pos['trail_stop_pnl_pct']``
and returns an exit reason string when current P&L has fallen to the stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TrailConfig:
    leverage: float = 20.0
    stop_pnl_pct: float = 24.0
    ratchet_step_pnl_pct: float = 10.0
    tp_trigger_pnl_pct: float = 45.0
    peak_trail_pnl_pct: float = 6.0
    # Richard (2026-09-25): "instead of percentage lets use pips". When > 0 the
    # stop is a fixed PRICE distance -- this % of the entry price, i.e. a fixed
    # number of points for the trade (BTC ~84,000 -> ~1,350 pts at 1.6) --
    # and moves one point for every point the price moves in the trade's
    # favour, like the India trails. It replaces the P&L-% stop/ratchet/floor.
    point_trail_pct: float = 0.0


def pnl_pct(entry: float, price: float, side: str, leverage: float) -> float:
    if entry <= 0:
        return 0.0
    direction = 1.0 if side == "long" else -1.0
    return (price / entry - 1.0) * direction * leverage * 100.0


def stop_level(peak: float, cfg: TrailConfig) -> float:
    """The P&L% at which the position should be closed, given the peak P&L% so far."""
    peak = max(0.0, peak)
    if peak < cfg.tp_trigger_pnl_pct - 1e-6:
        step = max(cfg.ratchet_step_pnl_pct, 1e-9)
        return -cfg.stop_pnl_pct + cfg.ratchet_step_pnl_pct * math.floor(peak / step + 1e-6)
    return max(cfg.tp_trigger_pnl_pct, peak - cfg.peak_trail_pnl_pct)


def _point_trail(pos: dict, price: float, cfg: TrailConfig) -> str | None:
    entry, price = float(pos["entry_price"]), float(price)
    long = pos["side"] == "long"
    dist = entry * cfg.point_trail_pct / 100.0
    best = float(pos.get("best_price") or entry)
    best = max(best, price) if long else min(best, price)
    stop = best - dist if long else best + dist
    pos["best_price"], pos["trail_stop_price"] = best, stop
    # keep the P&L-% fields the dashboard and journal already show
    pos["peak_pnl_pct"] = round(max(0.0, pnl_pct(entry, best, pos["side"], cfg.leverage)), 2)
    pos["trail_stop_pnl_pct"] = round(pnl_pct(entry, stop, pos["side"], cfg.leverage), 2)
    if (long and price <= stop) or (not long and price >= stop):
        locked = (stop - entry) if long else (entry - stop)
        return (f"point trail: {dist:,.6g} pts behind best {best:,.6g} "
                f"(stop {stop:,.6g}, {locked:+,.6g} pts from entry)")
    return None


def update_and_check(pos: dict, price: float, cfg: TrailConfig) -> str | None:
    if cfg.point_trail_pct > 0:
        return _point_trail(pos, price, cfg)
    cur = pnl_pct(float(pos["entry_price"]), float(price), pos["side"], cfg.leverage)
    peak = max(float(pos.get("peak_pnl_pct", 0.0)), cur, 0.0)
    pos["peak_pnl_pct"] = round(peak, 2)
    stop = stop_level(peak, cfg)
    pos["trail_stop_pnl_pct"] = round(stop, 2)
    if cur <= stop:
        kind = "trailing profit" if stop >= 0 else "trailing stop"
        return f"{kind} {stop:+.0f}% P&L (peak {peak:+.0f}%, now {cur:+.0f}%)"
    return None


def bracket_stop_price(entry: float, side: str, cfg: TrailConfig) -> float | None:
    """Absolute price for the exchange bracket stop — the -stop_pnl_pct level,
    a server-down backstop at the same place the engine's initial stop sits."""
    if entry <= 0 or cfg.leverage <= 0:
        return None
    frac = (cfg.point_trail_pct / 100.0 if cfg.point_trail_pct > 0
            else cfg.stop_pnl_pct / (100.0 * cfg.leverage))
    return round(entry * (1 - frac) if side == "long" else entry * (1 + frac), 2)


if __name__ == "__main__":  # self-check — walk a long trade through the whole path
    # pin the classic values so this tests the ratchet / floor math, not the
    # (retuned) module defaults
    cfg = TrailConfig(
        leverage=100.0, stop_pnl_pct=10.0, ratchet_step_pnl_pct=5.0,
        tp_trigger_pnl_pct=25.0, peak_trail_pnl_pct=2.0,
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
    update_and_check(pos2, entry * 1.004, cfg)   # +40% P&L
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
    print("crypto.strategies.trailing self-check ok")
