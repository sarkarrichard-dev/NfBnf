from __future__ import annotations

from typing import Any

from index_ai.config import RiskSettings
from index_ai.learning import today_live_consecutive_loss_streak, today_live_realized_pnl
from index_ai.risk_policy import DAILY_LOSS_RUPEES_PER_LOT, effective_risk_limits
from index_ai.strategies.credit_spread import CREDIT_ACTIONS
from index_ai.strategies.premium_sell import is_premium_sell_action


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
    """Daily kill switch for LIVE only: 3 consecutive losses or scaled daily loss budget."""
    limits = effective_risk_limits()
    realized = today_live_realized_pnl()
    streak = today_live_consecutive_loss_streak()
    loss_limit = abs(float(limits["max_daily_loss_rupees"]))
    streak_limit = int(limits["max_consecutive_losing_trades"] or risk.max_losing_trades_per_day)
    loss_budget_hit = realized <= -loss_limit
    loss_streak_hit = streak >= streak_limit
    triggered = loss_budget_hit or loss_streak_hit
    reasons: list[str] = []
    if loss_budget_hit:
        reasons.append(
            f"Daily loss ₹{abs(realized):,.0f} reached limit ₹{loss_limit:,.0f} "
            f"({int(limits['lots_per_trade'])} lot(s) × ₹{DAILY_LOSS_RUPEES_PER_LOT:,.0f})."
        )
    if loss_streak_hit:
        reasons.append(f"{streak} consecutive losing trades today (limit {streak_limit}).")
    live_only = risk.trading_mode == "LIVE"
    return {
        "active": triggered and live_only,
        "triggered": triggered,
        "live_only": True,
        "applies_when": "LIVE",
        "reasons": reasons,
        "today_realized_pnl": realized,
        "today_consecutive_loss_streak": streak,
        "today_losing_trades": streak,
        "max_daily_loss_rupees": loss_limit,
        "daily_loss_rupees_per_lot": DAILY_LOSS_RUPEES_PER_LOT,
        "lots_per_trade": limits["lots_per_trade"],
        "max_losing_trades_per_day": streak_limit,
        "max_consecutive_losing_trades": streak_limit,
    }


def check_execution_gates(
    *,
    risk: RiskSettings,
    signal_action: str,
    transaction_type: str,
    confidence: float,
    min_confidence: float,
    strategy_mode: str = "",
) -> tuple[bool, str]:
    from index_ai.market_clock import is_trading_entries_allowed, trading_window_message

    if risk.trading_mode == "LIVE" and not risk.allow_live_trading:
        return False, "Live mode is blocked by ALLOW_LIVE_TRADING=false."

    if not is_trading_entries_allowed():
        return False, trading_window_message()
    if risk.trading_mode == "LIVE":
        ks = kill_switch_state(risk)
        if ks["active"]:
            return False, "Kill switch active: " + " ".join(ks["reasons"])

    if signal_action == "NO_TRADE":
        return False, "No trade setup."

    if signal_action in CREDIT_ACTIONS or is_premium_sell_action(signal_action):
        if not risk.allow_option_selling:
            return False, "Option selling structures require allow_option_selling."
        tx = "SELL"

    if confidence < min_confidence:
        return False, "Confidence is below risk gate."

    tx = transaction_type.upper()
    if tx == "BUY" and not risk.allow_option_buying:
        return False, "Option buying is disabled."
    if tx == "SELL" and not risk.allow_option_selling:
        return False, "Option selling is disabled."

    return True, "Plan passed risk gates."
