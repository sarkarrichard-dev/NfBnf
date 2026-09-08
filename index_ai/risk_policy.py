"""Hard-coded trading risk policy (not configurable from the dashboard)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardcodedRiskPolicy:
    allow_option_buying: bool = True
    allow_option_selling: bool = True
    max_losing_trades_per_day: int = 3
    max_daily_loss_rupees: float = 6000.0
    # Legacy summary field; per-index trails live on IndexInstrument (activation + distance).
    trailing_stop_index_points: float = 40.0
    # Option-buying entry gate. Buys are swift directional scalps — hold them to a
    # higher bar than credit selling (whose gate is credit_confidence_gate ~0.45).
    min_confidence: float = 0.60
    max_profit_cap_rupees: float | None = None
    default_option_transaction: str = "BUY"
    lots_per_trade: int = 1


HARDCODED_RISK = HardcodedRiskPolicy()

# Per NSE lot (dashboard lots-per-trade multiplies these for the session).
DAILY_LOSS_RUPEES_PER_LOT = HARDCODED_RISK.max_daily_loss_rupees


def effective_risk_limits() -> dict[str, int | float | None]:
    from index_ai.trade_lots import get_lots_per_trade

    lots = get_lots_per_trade()
    p = HARDCODED_RISK
    profit_cap = p.max_profit_cap_rupees
    return {
        "lots_per_trade": lots,
        "daily_loss_rupees_per_lot": DAILY_LOSS_RUPEES_PER_LOT,
        "max_daily_loss_rupees": DAILY_LOSS_RUPEES_PER_LOT * lots,
        "max_profit_cap_rupees": (profit_cap * lots) if profit_cap is not None else None,
        "max_consecutive_losing_trades": p.max_losing_trades_per_day,
    }


def policy_summary() -> dict[str, str | int | float | None]:
    from index_ai.instruments import instruments
    from index_ai.trade_lots import get_lots_per_trade, lots_settings_summary

    p = HARDCODED_RISK
    limits = effective_risk_limits()
    lots = {key: inst.lot_size for key, inst in instruments().items()}
    trade_lots = lots_settings_summary()
    return {
        "lots_per_trade": get_lots_per_trade(),
        "lots_min": trade_lots["min_lots"],
        "lots_max": trade_lots["max_lots"],
        "order_quantities": {k: v["order_quantity"] for k, v in trade_lots["per_index"].items()},
        "index_lot_sizes": lots,
        "buy_options": p.allow_option_buying,
        "sell_options": p.allow_option_selling,
        "max_losing_trades_per_day": p.max_losing_trades_per_day,
        "max_consecutive_losing_trades": p.max_losing_trades_per_day,
        "daily_loss_rupees_per_lot": limits["daily_loss_rupees_per_lot"],
        "max_daily_loss_rupees": limits["max_daily_loss_rupees"],
        "trailing_stop_index_points": p.trailing_stop_index_points,
        "trailing_mode": "NIFTY: 100pt initial → arm after +25pt → 40pt trail; BANKNIFTY: 200 / +50 / 80",
        "min_confidence": p.min_confidence,
        "max_profit_cap_rupees": limits["max_profit_cap_rupees"],
        "default_transaction": p.default_option_transaction,
        "risk_scales_with_lots": True,
    }
