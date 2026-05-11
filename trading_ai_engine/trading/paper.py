from __future__ import annotations

from typing import Any

from trading_ai_engine.server import db
from trading_ai_engine.trading.risk import build_trade_plan


def plan_from_analysis(result: dict[str, Any]) -> dict[str, Any]:
    return build_trade_plan(
        symbol=str(result.get("symbol") or ""),
        brain=result.get("brain") or {},
        metrics=result.get("metrics") or {},
    )


def place_paper_order(
    *,
    finding_id: str,
    symbol: str,
    plan: dict[str, Any],
    brain: dict[str, Any],
) -> dict[str, Any]:
    if not plan.get("eligible"):
        return {
            "status": "rejected",
            "reason": "risk_veto",
            "vetoes": plan.get("vetoes") or [],
            "plan": plan,
        }

    order = {
        "finding_id": finding_id,
        "symbol": symbol,
        "side": plan["side"],
        "quantity": int(plan["quantity"]),
        "entry_price": float(plan["entry_price"]),
        "stop_loss": plan.get("stop_loss"),
        "target": plan.get("target"),
        "notional": float(plan["notional"]),
        "risk_amount": float(plan["risk_amount"]),
        "status": "filled_paper",
        "reason": plan.get("reason") or "",
        "plan": plan,
        "brain": brain,
    }
    order_id = db.insert_paper_order(order)
    db.insert_evolution_event(
        symbol=symbol,
        event_type="paper_order",
        score_delta=float(brain.get("feedback_effect") or 0.0),
        payload={"order_id": order_id, "plan": plan, "brain": brain},
    )
    return {"status": "filled_paper", "order_id": order_id, "plan": plan}


def recent_paper_orders(limit: int = 50) -> dict[str, Any]:
    return {"orders": db.fetch_paper_orders(limit=limit), "summary": db.paper_trading_summary()}
