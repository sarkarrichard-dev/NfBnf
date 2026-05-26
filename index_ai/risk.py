from __future__ import annotations

from typing import Any

from index_ai.config import RiskSettings
from index_ai.learning import today_losing_trades_count, today_realized_pnl


def risk_settings_dict(risk: RiskSettings) -> dict[str, Any]:
    return {
        "trading_mode": risk.trading_mode,
        "allow_live_trading": risk.allow_live_trading,
        "allow_option_buying": risk.allow_option_buying,
        "allow_option_selling": risk.allow_option_selling,
        "max_losing_trades_per_day": risk.max_losing_trades_per_day,
        "max_daily_loss_rupees": risk.max_daily_loss_rupees,
        "trailing_stop_index_points": risk.trailing_stop_index_points,
        "min_confidence": risk.min_confidence,
        "max_profit_cap_rupees": risk.max_profit_cap_rupees,
    }


def kill_switch_state(risk: RiskSettings) -> dict[str, Any]:
    """Daily kill switch for LIVE only: 3 closed losses or daily loss budget."""
    realized = today_realized_pnl()
    losses = today_losing_trades_count()
    loss_limit = abs(risk.max_daily_loss_rupees)
    loss_budget_hit = realized <= -loss_limit
    loss_streak_hit = losses >= risk.max_losing_trades_per_day
    triggered = loss_budget_hit or loss_streak_hit
    reasons: list[str] = []
    if loss_budget_hit:
        reasons.append(
            f"Daily loss ₹{abs(realized):,.0f} reached limit ₹{loss_limit:,.0f}."
        )
    if loss_streak_hit:
        reasons.append(
            f"{losses} losing trades today (limit {risk.max_losing_trades_per_day})."
        )
    live_only = risk.trading_mode == "LIVE"
    return {
        "active": triggered and live_only,
        "triggered": triggered,
        "live_only": True,
        "applies_when": "LIVE",
        "reasons": reasons,
        "today_realized_pnl": realized,
        "today_losing_trades": losses,
        "max_daily_loss_rupees": loss_limit,
        "max_losing_trades_per_day": risk.max_losing_trades_per_day,
    }


def check_execution_gates(
    *,
    risk: RiskSettings,
    signal_action: str,
    transaction_type: str,
    confidence: float,
    min_confidence: float,
) -> tuple[bool, str]:
    if risk.trading_mode == "LIVE":
        ks = kill_switch_state(risk)
        if ks["active"]:
            return False, "Kill switch active: " + " ".join(ks["reasons"])

    if signal_action == "NO_TRADE":
        return False, "No aligned CPR/EMA setup."

    if confidence < min_confidence:
        return False, "Confidence is below risk gate."

    tx = transaction_type.upper()
    if tx == "BUY" and not risk.allow_option_buying:
        return False, "Option buying is disabled."
    if tx == "SELL" and not risk.allow_option_selling:
        return False, "Option selling is disabled."

    if risk.trading_mode == "LIVE" and not risk.allow_live_trading:
        return False, "Live mode is blocked by ALLOW_LIVE_TRADING=false."

    return True, "Plan passed risk gates."
