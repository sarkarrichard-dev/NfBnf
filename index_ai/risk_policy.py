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
    min_confidence: float = 0.55
    max_profit_cap_rupees: float | None = None
    default_option_transaction: str = "BUY"
    lots_per_trade: int = 1


HARDCODED_RISK = HardcodedRiskPolicy()


def policy_summary() -> dict[str, str | int | float | None]:
    from index_ai.instruments import instruments

    p = HARDCODED_RISK
    lots = {key: inst.lot_size for key, inst in instruments().items()}
    return {
        "lots_per_trade": p.lots_per_trade,
        "index_lot_sizes": lots,
        "buy_options": p.allow_option_buying,
        "sell_options": p.allow_option_selling,
        "max_losing_trades_per_day": p.max_losing_trades_per_day,
        "max_daily_loss_rupees": p.max_daily_loss_rupees,
        "trailing_stop_index_points": p.trailing_stop_index_points,
        "trailing_mode": "NIFTY: 100pt initial → arm after +25pt → 40pt trail; BANKNIFTY: 200 / +50 / 80",
        "min_confidence": p.min_confidence,
        "max_profit_cap_rupees": p.max_profit_cap_rupees,
        "default_transaction": p.default_option_transaction,
    }
