from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from trading_ai_engine.india.nse_yahoo import normalize_nse_yahoo_symbol
from trading_ai_engine.market_yfinance import last_daily_close
from trading_ai_engine.ml.market_learn import load_market_model
from trading_ai_engine.server import db
from trading_ai_engine.trading.paper_gates import paper_placement_allowed
from trading_ai_engine.trading.risk import build_trade_plan, load_risk_config


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
    symbol = normalize_nse_yahoo_symbol(symbol.strip())
    if not plan.get("eligible"):
        return {
            "status": "rejected",
            "reason": "risk_veto",
            "vetoes": plan.get("vetoes") or [],
            "plan": plan,
        }

    cfg = load_risk_config()
    ok, gate_reason, gate_meta = paper_placement_allowed(plan=plan, cfg=cfg)
    if not ok:
        return {
            "status": "rejected",
            "reason": gate_reason,
            "gate_meta": gate_meta,
            "plan": plan,
        }

    model = load_market_model()
    model_version = (model or {}).get("version")

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
        "model_version": model_version,
    }
    order_id = db.insert_paper_order(order)
    db.insert_evolution_event(
        symbol=symbol,
        event_type="paper_order",
        score_delta=float(brain.get("feedback_effect") or 0.0),
        payload={
            "order_id": order_id,
            "plan": plan,
            "brain": brain,
            "model_version": model_version,
        },
    )
    return {"status": "filled_paper", "order_id": order_id, "plan": plan}


def close_paper_order(*, order_id: str, exit_price: Any = None) -> dict[str, Any]:
    """
    Mark a ``filled_paper`` row closed at ``exit_price`` (or Yahoo last daily close if omitted).
    ``realized_pnl`` is cash PnL in rupee terms (quantity * directional price change).
    """
    oid = str(order_id or "").strip()
    if not oid:
        return {"status": "error", "reason": "missing_order_id"}
    try:
        uuid.UUID(oid)
    except ValueError:
        return {"status": "error", "reason": "invalid_order_id"}
    row = db.fetch_paper_order_by_id(oid)
    if not row:
        return {"status": "error", "reason": "order_not_found"}
    if row.get("status") == "closed_paper":
        return {"status": "error", "reason": "already_closed"}
    if row.get("status") != "filled_paper":
        return {"status": "error", "reason": "not_closable", "order_status": row.get("status")}

    ep: float | None
    if exit_price is None or exit_price == "":
        ep = last_daily_close(str(row["symbol"]))
        if ep is None:
            return {"status": "error", "reason": "exit_price_required_no_yahoo_data"}
    else:
        try:
            ep = float(exit_price)
        except (TypeError, ValueError):
            return {"status": "error", "reason": "invalid_exit_price"}
        if ep <= 0:
            return {"status": "error", "reason": "invalid_exit_price"}

    entry = float(row["entry_price"])
    qty = int(row["quantity"])
    side = str(row["side"] or "").lower()
    if side == "long":
        pnl = (ep - entry) * qty
    elif side == "short":
        pnl = (entry - ep) * qty
    else:
        return {"status": "error", "reason": "unknown_side", "side": row.get("side")}

    exit_at = datetime.now(timezone.utc).isoformat()
    ok = db.close_paper_order_row(order_id=oid, exit_price=ep, exit_at=exit_at, realized_pnl=pnl)
    if not ok:
        return {"status": "error", "reason": "close_race_or_state_changed"}

    notional = max(float(row.get("notional") or 0.0), 1.0)
    db.insert_evolution_event(
        symbol=str(row["symbol"]),
        event_type="paper_close",
        score_delta=float(round(100.0 * pnl / notional, 6)),
        payload={
            "order_id": oid,
            "exit_price": ep,
            "realized_pnl": pnl,
            "side": side,
        },
    )
    return {
        "status": "closed_paper",
        "order_id": oid,
        "exit_price": ep,
        "exit_at": exit_at,
        "realized_pnl": round(pnl, 4),
    }


def recent_paper_orders(limit: int = 50) -> dict[str, Any]:
    return {"orders": db.fetch_paper_orders(limit=limit), "summary": db.paper_trading_summary()}
