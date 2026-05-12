from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from trading_ai_engine.india.nse_yahoo import normalize_nse_yahoo_symbol
from trading_ai_engine.market_yfinance import last_daily_close
from trading_ai_engine.ml.market_learn import load_market_model
from trading_ai_engine.openalgo.client import place_smart_order
from trading_ai_engine.openalgo.config import load_openalgo_config, openalgo_orders_disabled_by_env
from trading_ai_engine.openalgo.symbols import yahoo_to_openalgo
from trading_ai_engine.server import db
from trading_ai_engine.trading.execution_mode import (
    MODE_LIVE_DHAN,
    MODE_PAPER_LOCAL,
    MODE_PAPER_OPENALGO,
    get_execution_mode,
)
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
    mode = get_execution_mode()

    if mode == MODE_LIVE_DHAN:
        return {
            "status": "rejected",
            "reason": "live_dhan_router_not_implemented",
            "execution_mode": mode,
            "plan": plan,
            "detail": (
                "Native Dhan order placement is not wired in this build. "
                "Keep execution on paper_openalgo (OpenAlgo Analyzer/sandbox) or paper_local until it is."
            ),
        }

    execution_channel = "local"
    external_order_id: str | None = None
    base_reason = plan.get("reason") or ""

    if mode == MODE_PAPER_OPENALGO:
        if openalgo_orders_disabled_by_env():
            return {
                "status": "rejected",
                "reason": "openalgo_disabled_by_env",
                "execution_mode": mode,
                "plan": plan,
            }
        oac = load_openalgo_config()
        if not oac.ready:
            return {
                "status": "rejected",
                "reason": "openalgo_not_configured",
                "execution_mode": mode,
                "plan": plan,
                "detail": "Set OPENALGO_BASE_URL and OPENALGO_API_KEY (or local_secrets).",
            }
        inst = str(plan.get("instrument_type") or "equity")
        try:
            oa_sym, oa_ex = yahoo_to_openalgo(symbol, instrument_type=inst)
        except ValueError as e:
            return {
                "status": "rejected",
                "reason": "openalgo_symbol_unmapped",
                "execution_mode": mode,
                "plan": plan,
                "detail": str(e),
            }
        side = str(plan.get("side") or "").lower()
        if side not in ("long", "short"):
            return {
                "status": "rejected",
                "reason": "openalgo_needs_directional_side",
                "execution_mode": mode,
                "plan": plan,
            }
        action = "BUY" if side == "long" else "SELL"
        product = oac.default_product_fno if inst == "fno" else oac.default_product_equity
        oa_resp = place_smart_order(
            symbol=oa_sym,
            exchange=oa_ex,
            action=action,
            quantity=int(plan["quantity"]),
            position_size=0.0,
            product=product,
            cfg=oac,
        )
        oa_status = str(oa_resp.get("status") or "").lower()
        ext = oa_resp.get("orderid") or oa_resp.get("order_id")
        ext_s = str(ext).strip() if ext is not None else ""
        http_ok = int(oa_resp.get("http_status") or 0) in range(200, 300)
        broker_ok = oa_status == "success" or bool(ext_s)
        if not (http_ok and broker_ok):
            return {
                "status": "rejected",
                "reason": "openalgo_order_rejected",
                "execution_mode": mode,
                "plan": plan,
                "openalgo": oa_resp,
            }
        execution_channel = "openalgo"
        external_order_id = ext_s or None
        base_reason = f"{base_reason} [OpenAlgo {oa_sym}/{oa_ex} orderid={ext_s or '?'}]".strip()

    elif mode != MODE_PAPER_LOCAL:
        return {
            "status": "rejected",
            "reason": "unknown_execution_mode",
            "execution_mode": mode,
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
        "reason": base_reason,
        "plan": plan,
        "brain": brain,
        "model_version": model_version,
        "execution_channel": execution_channel,
        "external_order_id": external_order_id,
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
            "execution_mode": mode,
            "execution_channel": execution_channel,
            "external_order_id": external_order_id,
        },
    )
    return {
        "status": "filled_paper",
        "order_id": order_id,
        "plan": plan,
        "execution_mode": mode,
        "execution_channel": execution_channel,
        "external_order_id": external_order_id,
    }


def close_paper_order(*, order_id: str, exit_price: Any = None) -> dict[str, Any]:
    """
    Mark a ``filled_paper`` row closed at ``exit_price`` (or Yahoo last daily close if omitted).
    ``realized_pnl`` is cash PnL in rupee terms (quantity * directional price change).

    Rows routed via OpenAlgo still use this SQLite lifecycle for AIML / desk tracking; closing
    the broker leg on OpenAlgo (if any) is separate and may be done from the OpenAlgo UI/API.
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
