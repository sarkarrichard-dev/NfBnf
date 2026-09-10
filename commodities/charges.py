"""MCX commodity-futures cost model, from the Dhan schedule (dhan.co/pricing):

    Brokerage          ₹20 per executed order (flat)
    Transaction (MCX)  0.0021% of turnover, per side
    CTT                0.01% on the SELL side
    SEBI turnover      0.0001% of turnover, per side
    Stamp duty         0.002% on the BUY side
    GST                18% on (brokerage + transaction + SEBI)

Turnover per side = price × contract multiplier × lots. The multiplier lives on
`CommoditySpec` (₹ P&L per 1.0 price move, per lot).
"""

from __future__ import annotations

import os

from commodities.instruments import CommoditySpec
from index_ai.charges import ChargeRates

MCX_FUT_TXN_PCT = 0.000021    # 0.0021%
MCX_CTT_SELL_PCT = 0.0001     # 0.01%, sell side
MCX_STAMP_BUY_PCT = 0.00002   # 0.002%, buy side


def _turnover(price: float, spec: CommoditySpec, lots: int) -> float:
    return max(0.0, float(price)) * spec.multiplier * max(0, int(lots))


def round_trip_cost_rupees(
    entry_price: float, exit_price: float, spec: CommoditySpec, lots: int,
    *, rates: ChargeRates | None = None,
) -> float:
    """Open + close statutory / broker cost for `lots` of this contract (no
    slippage — see `slippage_rupees`)."""
    r = rates or ChargeRates.load()
    buy_t = _turnover(entry_price, spec, lots)
    sell_t = _turnover(exit_price, spec, lots)
    if buy_t <= 0 or sell_t <= 0:
        return 0.0
    txn_pct = float(os.getenv("CHARGE_MCX_FUT_TXN_PCT", str(MCX_FUT_TXN_PCT)))
    ctt_pct = float(os.getenv("CHARGE_MCX_CTT_SELL_PCT", str(MCX_CTT_SELL_PCT)))
    stamp_pct = float(os.getenv("CHARGE_MCX_STAMP_BUY_PCT", str(MCX_STAMP_BUY_PCT)))

    brokerage = 2 * r.brokerage_per_order_rupees            # one entry order + one exit order
    txn = txn_pct * (buy_t + sell_t)
    sebi = r.sebi_pct * (buy_t + sell_t)
    ctt = ctt_pct * sell_t
    stamp = stamp_pct * buy_t
    gst = r.gst_pct * (brokerage + txn + sebi)
    return round(brokerage + txn + sebi + ctt + stamp + gst, 2)


def slippage_rupees(spec: CommoditySpec, lots: int) -> float:
    """One tick of half-spread each side, ₹. `SLIPPAGE_MCX_TICKS_<KEY>` overrides
    the 1.0-tick default."""
    ticks = 1.0
    env = os.getenv(f"SLIPPAGE_MCX_TICKS_{spec.key}")
    if env:
        try:
            ticks = max(0.0, float(env))
        except ValueError:
            pass
    return ticks * spec.tick * spec.multiplier * max(0, int(lots)) * 2.0


if __name__ == "__main__":  # self-check
    from commodities.instruments import BY_KEY

    crude = BY_KEY["CRUDEOILM"]
    # ~₹6,000/bbl, 10-bbl contract → ~₹60k turnover/side
    cost = round_trip_cost_rupees(6000.0, 6060.0, crude, 1)
    slip = slippage_rupees(crude, 1)
    assert 40 < cost < 120, cost           # brokerage ₹40 + a few ₹ of statutory
    assert slip == 20.0, slip              # 1 tick (₹1) × 10 multiplier × 2 sides
    # a ₹60 up-move on 10× multiplier = ₹600 gross, comfortably clears ~₹75 total
    gross = (6060.0 - 6000.0) * crude.multiplier
    assert gross - cost - slip > 400
    print(f"commodities.charges self-check ok - crude 1 lot round trip ~Rs {cost + slip:.0f}")
