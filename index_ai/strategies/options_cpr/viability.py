"""
Is a structure economically viable on this index, at its *measured* costs?

A strategy is only tradable if the edge it produces clears what it costs to
trade. Both sides of that are now measurable, so the platform can answer it per
index instead of relying on someone remembering which index "doesn't work":

    friction_floor()  what one round trip of this structure costs, from measured
                      per-leg spreads plus the real Dhan charge schedule
    viability()       that floor against the gross edge per trade observed in the
                      backtest, giving a plain verdict

Measured on 2026-08-29 (last ~19 months, 1 trade/day, hedged directional sell):

    NIFTY      gross/trade  +Rs 211  vs floor ~Rs 152  -> VIABLE
    BANKNIFTY  gross/trade  +Rs 229  vs floor ~Rs 595  -> NOT VIABLE (4x lot,
               widest book of the three; the edge is real, the structure is wrong)
    SENSEX     unmeasured — the BSE chain returned no book depth, so its floor is
               a default, not an observation

BANKNIFTY's problem is leg count, not signal: dropping to a single short leg
halves friction and takes it from PF 0.64 to 0.94. That structure needs margin a
small account does not have, so the honest answer for BANKNIFTY today is "not on
this structure", not "no edge".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from index_ai.charges import leg_charge_rupees
from index_ai.strategies.options_cpr.config import OptionsCprConfig, config_for

VIABLE = "VIABLE"
MARGINAL = "MARGINAL"
NOT_VIABLE = "NOT_VIABLE"
UNMEASURED = "UNMEASURED"

# gross edge must clear the floor by this much before a lane is called viable
VIABLE_MULTIPLE = 1.30
MARGINAL_MULTIPLE = 1.00

# per-trade gross edge observed in backtest (rupees), last measured 2026-08-29.
# NOTE: the friction floor below is live-measured (spread_calib), but this side is
# a fixed constant. Now that entry_guard uses this verdict to gate the LIVE sell
# lane, a spread widening can flip an index to NOT_VIABLE against a stale edge
# number — re-measure from the SQLite journal when live volume allows.
OBSERVED_GROSS_PER_TRADE: dict[tuple[str, str], float] = {
    ("NIFTY", "sell"): 211.0,
    ("BANKNIFTY", "sell"): 229.0,
    ("SENSEX", "sell"): 300.0,
    ("NIFTY", "buy"): -51.0,
    ("BANKNIFTY", "buy"): -197.0,
    ("SENSEX", "buy"): -80.0,
}


@dataclass(frozen=True)
class Viability:
    instrument: str
    lane: str
    legs: int
    friction_floor_rupees: float
    gross_per_trade_rupees: float | None
    edge_multiple: float | None
    verdict: str
    spread_source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "lane": self.lane,
            "legs": self.legs,
            "friction_floor_rupees": round(self.friction_floor_rupees, 2),
            "gross_per_trade_rupees": self.gross_per_trade_rupees,
            "edge_multiple": self.edge_multiple,
            "verdict": self.verdict,
            "spread_source": self.spread_source,
            "reason": self.reason,
        }


def friction_floor(
    cfg: OptionsCprConfig, *, lane: str = "sell", typical_premium: float | None = None
) -> tuple[float, int, str]:
    """(round-trip cost in rupees, leg count, spread source) for one lot."""
    from index_ai.market_context.spread_calib import bucket_half_spreads, calibrated_half_spread

    near_hs, wing_hs = bucket_half_spreads(cfg.key)
    _hs, src = calibrated_half_spread(cfg.key)
    lot = cfg.lot_size
    prem = typical_premium if typical_premium is not None else max(cfg.strike_step * 1.2, 60.0)

    if lane == "buy":
        charges = leg_charge_rupees(prem, lot, "BUY", exchange=cfg.exchange) + leg_charge_rupees(
            prem, lot, "SELL", exchange=cfg.exchange
        )
        return charges + near_hs * lot * 2, 1, src

    legs = 1 if cfg.sell_naked else 2
    charges = leg_charge_rupees(prem, lot, "SELL", exchange=cfg.exchange) + leg_charge_rupees(
        prem, lot, "BUY", exchange=cfg.exchange
    )
    slip = near_hs * lot * 2
    if legs == 2:
        wing_prem = max(prem * 0.05, 2.0)
        charges += leg_charge_rupees(
            wing_prem, lot, "BUY", exchange=cfg.exchange
        ) + leg_charge_rupees(wing_prem, lot, "SELL", exchange=cfg.exchange)
        slip += wing_hs * lot * 2
    return charges + slip, legs * 2, src


def viability(
    instrument: str,
    lane: str = "sell",
    *,
    cfg: OptionsCprConfig | None = None,
    gross_per_trade: float | None = None,
) -> Viability:
    c = cfg or config_for(instrument)
    key = c.key
    floor, legs, src = friction_floor(c, lane=lane)
    gross = (
        gross_per_trade
        if gross_per_trade is not None
        else OBSERVED_GROSS_PER_TRADE.get((key, lane))
    )

    if "default" in src:
        return Viability(
            key,
            lane,
            legs,
            floor,
            gross,
            None,
            UNMEASURED,
            src,
            f"{key} spread is a default, not an observation — cost is a guess "
            f"until the live book is sampled",
        )
    if gross is None:
        return Viability(
            key,
            lane,
            legs,
            floor,
            None,
            None,
            UNMEASURED,
            src,
            "no measured gross edge for this lane yet",
        )
    if gross <= 0:
        return Viability(
            key,
            lane,
            legs,
            floor,
            gross,
            0.0,
            NOT_VIABLE,
            src,
            f"gross edge is negative (Rs {gross:.0f}/trade) — no cost level saves it",
        )

    mult = gross / max(floor, 1e-9)
    if mult >= VIABLE_MULTIPLE:
        verdict, why = VIABLE, f"gross Rs {gross:.0f}/trade covers Rs {floor:.0f} floor {mult:.2f}x"
    elif mult >= MARGINAL_MULTIPLE:
        verdict, why = (
            MARGINAL,
            f"gross Rs {gross:.0f}/trade barely clears Rs {floor:.0f} floor ({mult:.2f}x)",
        )
    else:
        verdict, why = (
            NOT_VIABLE,
            (
                f"gross Rs {gross:.0f}/trade cannot cover Rs {floor:.0f} floor "
                f"({legs} orders on a {'wide' if floor > 400 else 'normal'} book)"
            ),
        )
    return Viability(key, lane, legs, floor, gross, round(mult, 3), verdict, src, why)


def report(lanes: tuple[str, ...] = ("buy", "sell")) -> dict[str, Any]:
    out: dict[str, Any] = {"viable_multiple": VIABLE_MULTIPLE, "instruments": {}}
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        out["instruments"][key] = {ln: viability(key, ln).to_dict() for ln in lanes}
    return out


if __name__ == "__main__":  # ponytail self-check
    import os

    os.environ["SLIPPAGE_HALF_SPREAD_POINTS_NIFTY"] = "0.20"
    os.environ["SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY"] = "4.06"
    n = viability("NIFTY", "sell")
    b = viability("BANKNIFTY", "sell")
    print(f"NIFTY     sell: floor Rs {n.friction_floor_rupees:>7.0f}  {n.verdict:11s} {n.reason}")
    print(f"BANKNIFTY sell: floor Rs {b.friction_floor_rupees:>7.0f}  {b.verdict:11s} {b.reason}")
    assert b.friction_floor_rupees > n.friction_floor_rupees * 2, "BANKNIFTY book is far wider"
    assert b.verdict == NOT_VIABLE, b
    assert n.verdict in (VIABLE, MARGINAL), n
    # a negative-gross lane is never viable at any cost level
    assert viability("NIFTY", "buy").verdict == NOT_VIABLE
    # naked halves the order count
    from index_ai.strategies.options_cpr.config import with_overrides

    naked = with_overrides(config_for("BANKNIFTY"), sell_naked=True)
    assert friction_floor(naked, lane="sell")[1] == 2
    assert friction_floor(naked, lane="sell")[0] < b.friction_floor_rupees
    # unmeasured spread must not be reported as a verdict
    for k in ("SLIPPAGE_HALF_SPREAD_POINTS_NIFTY", "SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY"):
        del os.environ[k]
    assert viability("SENSEX", "sell").verdict == UNMEASURED
    print("viability.py self-check ok")
