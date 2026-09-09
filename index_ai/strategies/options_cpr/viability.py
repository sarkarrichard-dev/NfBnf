"""
Is a structure economically viable on this index, at its *measured* costs?

A strategy is only tradable if the edge it produces clears what it costs to
trade. Both sides of that are now measurable, so the platform can answer it per
index instead of relying on someone remembering which index "doesn't work":

    friction_floor()  what one round trip of this structure costs, from measured
                      per-leg spreads plus the real Dhan charge schedule
    viability()       that floor against the gross edge per trade observed in the
                      backtest, giving a plain verdict

Re-measured 2026-09-09 from the real SQLite journal (mid-to-mid gross, no charges;
``scripts/measure_viability_gross.py`` recomputes it), hedged directional sell:

    NIFTY      gross/trade  +Rs 6    over 65 trades  (win 54%)  -> NOT_VIABLE
    BANKNIFTY  gross/trade  -Rs 178  over 77 trades  (win 39%)  -> NOT_VIABLE
    SENSEX     gross/trade  -Rs 57   over 36 trades  (win 33%)  -> UNMEASURED
               (BSE book depth still not sampled, so its floor is a default)

The 2026-08-29 backtest read +Rs 211 / +Rs 229 / +Rs 300 — that was BS-proxy
optimism (see strategy-findings on the six proxy versions). Live, the CPR + EMA
directional-sell lane has no gross edge: flat on NIFTY, negative on BANKNIFTY
and getting worse (first-half -Rs 51/trade, second-half -Rs 302). The
``entry_guard`` viability gate that consumes this verdict ships **default-off**
(``OPTIONS_REQUIRE_VIABLE``) — the lane's own signal was just retimed to 5m/15m
(PR #41) and hasn't earned its way back yet; re-measure once it has ~30 trades.
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

# per-trade gross edge (rupees). Sell lane: re-measured 2026-09-09 from the real
# journal (scripts/measure_viability_gross.py). Buy lane: still the 2026-08-29
# backtest — live buy volume is under 30 trades/index, too thin to re-measure.
# Re-run the script and update the sell rows once a lane clears ~30 forward trades.
OBSERVED_GROSS_PER_TRADE: dict[tuple[str, str], float] = {
    ("NIFTY", "sell"): 6.0,
    ("BANKNIFTY", "sell"): -178.0,
    ("SENSEX", "sell"): -57.0,
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
    # live gross: NIFTY +6 can't clear its floor, BANKNIFTY is negative
    assert b.verdict == NOT_VIABLE, b
    assert n.verdict == NOT_VIABLE, n
    # a positive gross that clears the floor is viable (explicit override)
    assert viability("NIFTY", "sell", gross_per_trade=400.0).verdict in (VIABLE, MARGINAL)
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
