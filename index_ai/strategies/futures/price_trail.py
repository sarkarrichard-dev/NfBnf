"""Two-phase trailing stop for the point-based / percent-of-price lanes
(index futures, stock futures, MCX commodities) -- the same shape as
crypto's P&L-percent trail (``crypto/strategies/trailing.py``), just in
absolute price points instead of percent of margin.

Richard, 2026-09-15: "I want all the segments should have a trailing stop
loss and trailing profit... that should be standard and no deviation is
allowed." Before this, futures and commodities each had one flat trailing
stop that armed once price moved favorably enough, then trailed the peak by
the same distance for the rest of the trade -- a single mechanism doing both
jobs. This gives them a real second phase: once the trade has run further in
its favor than the first phase needed to arm, the stop switches to a
*tighter* trail that locks in more of the gain -- a genuine trailing-profit
behaviour distinct from the wider trailing-stop phase, matching what crypto
already does (stop_pnl_pct ratchet, then a tighter tp_trigger_pnl_pct /
peak_trail_pnl_pct floor).

Callers own unit conversion (points for the index/stock-futures lane,
percent-of-entry-price for commodities) -- this module only works in
whatever absolute price points it's handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriceTrailLevels:
    trail_activate_pts: float  # favorable move (from entry) to arm phase 1
    trail_pts: float  # phase-1 trail distance behind the peak
    profit_trigger_pts: (
        float  # favorable move (from entry) to arm phase 2 -- must be > trail_activate_pts
    )
    profit_trail_pts: float  # phase-2 trail distance behind the peak -- tighter than trail_pts


def update_price_trail(pos: dict[str, Any], price: float, levels: PriceTrailLevels) -> bool:
    """Mutates ``pos['peak']`` / ``pos['stop']`` / ``pos['armed']`` /
    ``pos['profit_armed']`` in place. ``pos['dir']`` is ``"LONG"``/``"SHORT"``,
    ``pos['entry']`` the entry price. Returns True once ``price`` has reached
    the current stop level -- the caller closes the position at ``pos['stop']``.

    The stop only ever ratchets toward the trade's favour (``max``/``min``),
    same invariant as every other trail in this codebase -- phase 2 engaging
    can only tighten it, never loosen what phase 1 already locked in.
    """
    d = 1 if pos["dir"] == "LONG" else -1
    pos["peak"] = max(pos["peak"], price) if d == 1 else min(pos["peak"], price)
    peak_fav = (pos["peak"] - pos["entry"]) * d

    if not pos.get("armed") and peak_fav >= levels.trail_activate_pts:
        pos["armed"] = True
    if not pos.get("profit_armed") and peak_fav >= levels.profit_trigger_pts:
        pos["profit_armed"] = True

    if pos.get("profit_armed"):
        trail = pos["peak"] - d * levels.profit_trail_pts
        pos["stop"] = max(pos["stop"], trail) if d == 1 else min(pos["stop"], trail)
    elif pos.get("armed"):
        trail = pos["peak"] - d * levels.trail_pts
        pos["stop"] = max(pos["stop"], trail) if d == 1 else min(pos["stop"], trail)

    return (price <= pos["stop"]) if d == 1 else (price >= pos["stop"])


if __name__ == "__main__":  # self-check
    levels = PriceTrailLevels(
        trail_activate_pts=10.0, trail_pts=8.0, profit_trigger_pts=30.0, profit_trail_pts=3.0
    )

    # long: stays flat at the initial stop until phase 1 arms
    pos = {"dir": "LONG", "entry": 100.0, "peak": 100.0, "stop": 90.0}
    assert not update_price_trail(pos, 105.0, levels)  # +5, below the 10pt arm level
    assert not pos.get("armed") and pos["stop"] == 90.0

    # +12 arms phase 1 -- trail is peak(112) - 8 = 104
    assert not update_price_trail(pos, 112.0, levels)
    assert pos["armed"] and pos["stop"] == 104.0 and not pos.get("profit_armed")

    # +32 arms phase 2 (tighter) -- trail is peak(132) - 3 = 129, well past phase 1's line
    assert not update_price_trail(pos, 132.0, levels)
    assert pos["profit_armed"] and pos["stop"] == 129.0

    # pulls back to 129 -- phase-2 stop hit
    assert update_price_trail(pos, 129.0, levels)

    # short mirrors it
    pos2 = {"dir": "SHORT", "entry": 100.0, "peak": 100.0, "stop": 110.0}
    assert not update_price_trail(pos2, 88.0, levels)  # -12, arms phase 1
    assert pos2["armed"] and pos2["stop"] == 96.0
    assert not update_price_trail(pos2, 68.0, levels)  # -32, arms phase 2
    assert pos2["profit_armed"] and pos2["stop"] == 71.0
    assert update_price_trail(pos2, 71.0, levels)

    # the stop never loosens even if price momentarily pulls back inside an
    # already-armed level without hitting it
    pos3 = {"dir": "LONG", "entry": 100.0, "peak": 100.0, "stop": 90.0, "armed": True}
    pos3["stop"] = 104.0  # pretend phase 1 already ratcheted here
    update_price_trail(pos3, 105.0, levels)  # a small pullback, still above the stop
    assert pos3["stop"] == 104.0, "stop must not loosen on a mere pullback"

    print("index_ai.strategies.futures.price_trail self-check ok")
