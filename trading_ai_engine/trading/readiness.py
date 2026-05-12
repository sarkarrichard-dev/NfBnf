from __future__ import annotations

from typing import Any

from trading_ai_engine.ml.market_learn import data_quality_report, learning_status
from trading_ai_engine.research.readiness import REQUIRED_FOR_LIVE, bot_readiness_snapshot
from trading_ai_engine.server import db
from trading_ai_engine.trading.derivatives_focus import readiness_market_focus_block
from trading_ai_engine.trading.execution_mode import execution_snapshot
from trading_ai_engine.trading.paper_gates import kill_switch_active, paper_sessions_ist
from trading_ai_engine.trading.risk import load_risk_config


def workstation_readiness() -> dict[str, Any]:
    """
    Operator snapshot aligned with ``guides/Roadmap and Safety Guide.md``:
    data-quality hints, paper session count, kill switch, daily paper stats, catalog health.
    """
    snap = bot_readiness_snapshot()
    dq = data_quality_report()
    sessions = paper_sessions_ist()
    catalog = db.ml_datasets_summary()
    ls = learning_status()
    model = ls.get("model") or {}
    risk = load_risk_config()

    checklist: dict[str, Any] = {
        "data_quality_report_ok": dq.get("status") == "ok",
        "data_quality_report_warn_only": dq.get("status") == "warn",
        "ml_profile_catalog_zero_errors": int(catalog.get("errors") or 0) == 0,
        "paper_sessions_with_orders_ge_20": int(sessions.get("sessions_with_orders") or 0) >= 20,
        "market_model_exists": bool(model.get("version")),
        "training_frame_exists": bool((ls.get("training_frame") or {}).get("exists")),
    }

    snap["workstation_gates"] = {
        "market_focus": readiness_market_focus_block(),
        "kill_switch_active": kill_switch_active(),
        "paper_sessions_ist": sessions,
        "paper_today_ist": db.paper_stats_current_ist_day(),
        "risk_limits": {
            "max_trades_per_day": risk.max_trades_per_day,
            "max_daily_loss_pct": risk.max_daily_loss_pct,
            "daily_risk_budget_note": (
                "New paper orders are rejected when today's sum(risk_amount) in IST would exceed "
                "starting_equity * max_daily_loss_pct (committed-risk proxy until realised PnL exists)."
            ),
        },
        "data_quality": dq,
        "ml_profile_catalog": {
            "files": catalog.get("files"),
            "errors": catalog.get("errors"),
            "rows_profiled": catalog.get("rows_profiled"),
        },
        "checklist_preview": checklist,
        "execution": execution_snapshot(),
    }
    snap["required_for_live"] = REQUIRED_FOR_LIVE
    return snap
