from __future__ import annotations

import os
from typing import Any

from trading_ai_engine.server import db
from trading_ai_engine.trading.risk import RiskConfig, load_risk_config


def kill_switch_active() -> bool:
    """When true, all paper order placement is blocked (manual operator halt)."""
    return os.environ.get("TRADING_AI_KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on")


def paper_placement_allowed(
    *,
    plan: dict[str, Any],
    cfg: RiskConfig | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Enforce roadmap gates before inserting a paper order:

    - Kill switch
    - Max trades per calendar day (IST)
    - Max *committed risk* per IST day capped by starting_equity * max_daily_loss_pct
      (proxy for daily loss limit until realised PnL is tracked).
    """
    cfg = cfg or load_risk_config()
    meta: dict[str, Any] = {"kill_switch": kill_switch_active()}
    if kill_switch_active():
        return False, "kill_switch_active", meta

    day = db.paper_stats_current_ist_day()
    meta.update(day)
    n = int(day.get("orders_today") or 0)
    risk_so_far = float(day.get("risk_amount_today") or 0.0)
    new_risk = float(plan.get("risk_amount") or 0.0)
    daily_risk_cap = float(cfg.starting_equity) * float(cfg.max_daily_loss_pct)

    if n >= int(cfg.max_trades_per_day):
        return False, "max_trades_per_day", meta

    if risk_so_far + new_risk > daily_risk_cap + 1e-6:
        return False, "daily_risk_budget_exceeded", meta

    return True, "", meta


def paper_sessions_ist() -> dict[str, Any]:
    """Distinct IST calendar days with at least one paper order (roadmap: 20 sessions)."""
    return {
        "sessions_with_orders": db.paper_distinct_ist_session_days(),
        "roadmap_target_sessions": 20,
    }
