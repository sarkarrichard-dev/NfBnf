from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

from index_ai.analytics import build_analytics
from index_ai.config import DASHBOARD_DIR, MEMORY_DIR, set_trading_mode, settings
from index_ai.risk import kill_switch_state
from index_ai.risk_policy import HARDCODED_RISK, policy_summary
from index_ai.strategy_params import strategy_tuning_summary
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.dhan_auth import (
    auth_setup_checklist,
    check_dhan_health,
    generate_consent,
    jwt_token_status,
    reconcile_env_with_jwt,
    renew_access_token,
    save_token_from_user_input,
    verify_access_token,
)
from index_ai.executor import build_execution_plan, execute_plan
from index_ai.instruments import configured_index_keys, get_instrument, instruments, unconfigured_index_keys
from index_ai.learning import (
    format_trade_for_ui,
    init_db,
    learned_settings,
    learning_report,
    open_trades,
    recent_trades,
    record_feedback,
    record_trade_outcome,
    trades_summary,
    update_trade_trail_meta,
)
from index_ai.chart_live import fetch_supertrend_snapshot
from index_ai.trailing import evaluate_open_trade
from index_ai.heatmap import build_heatmap
from index_ai.planner import plan_instrument
from index_ai.market_clock import format_ist_display, market_status, now_ist_iso
from index_ai.scanner import clear_auth_block, scanner_status, start_scanner, stop_scanner
from index_ai.exit import close_open_trade


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from index_ai.learning import reconcile_all_trade_lots

    init_db()
    reconcile_all_trade_lots()
    yield


app = FastAPI(title="Index Options AI", version="0.1.0", lifespan=lifespan)

HEALTH_APP_ID = "index-options-ai"


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, Any]:
    """Lightweight probe so the dashboard can detect the correct server on port 8000."""
    return {"ok": True, "app": HEALTH_APP_ID, "version": app.version}


def _dhan_oauth_redirect(token_id: str | None) -> RedirectResponse:
    if not token_id or not str(token_id).strip():
        detail = quote("Missing tokenId in redirect URL.")
        return RedirectResponse(url=f"/?dhan_auth=error&detail={detail}", status_code=302)
    try:
        reconcile_env_with_jwt()
        save_token_from_user_input(settings().dhan, str(token_id).strip())
        clear_auth_block()
        return RedirectResponse(url="/?dhan_auth=success", status_code=302)
    except Exception as exc:
        return RedirectResponse(url=f"/?dhan_auth=error&detail={quote(str(exc))}", status_code=302)


@app.get("/dhan/oauth/callback", include_in_schema=False)
async def dhan_oauth_callback(
    token_id: str | None = Query(None, alias="tokenId"),
) -> RedirectResponse:
    """Dhan OAuth redirect target — exchanges tokenId and opens the dashboard."""
    return _dhan_oauth_redirect(token_id)


@app.get("/callback", include_in_schema=False)
async def dhan_oauth_callback_short(
    token_id: str | None = Query(None, alias="tokenId"),
) -> RedirectResponse:
    """Alias when redirect URL is http://127.0.0.1:8000/callback"""
    return _dhan_oauth_redirect(token_id)


def _dhan_setup_message() -> str:
    cfg = settings()
    if not cfg.dhan.app_credentials_ready:
        return "Dhan API key and secret are missing in .env."
    if not cfg.dhan.client_id:
        return "Dhan Client ID is missing in .env."
    if not cfg.dhan.access_token:
        return (
            "Dhan daily access token is missing. Use the Dhan Login panel: create the login "
            "link, complete Dhan login, then paste the returned tokenId."
        )
    return "Dhan is ready."


def _trading_gates(cfg: Any) -> dict[str, Any]:
    """Explain why live broker orders are blocked or allowed."""
    reasons: list[dict[str, str]] = []
    if not cfg.dhan.ready:
        reasons.append(
            {
                "title": "Dhan not ready",
                "detail": "Missing or expired Dhan token. Complete login in the Dhan panel first.",
            }
        )
    if cfg.risk.trading_mode != "LIVE":
        reasons.append(
            {
                "title": "Paper mode (default)",
                "detail": (
                    f"TRADING_MODE={cfg.risk.trading_mode}. Execute only logs trades to the local journal "
                    "(status PAPER_RECORDED); nothing is sent to Dhan."
                ),
            }
        )
    if not cfg.risk.allow_live_trading:
        reasons.append(
            {
                "title": "Live flag off",
                "detail": (
                    "ALLOW_LIVE_TRADING=false. Even with TRADING_MODE=LIVE, broker orders stay blocked "
                    "until you set ALLOW_LIVE_TRADING=true in .env."
                ),
            }
        )
    if cfg.risk.allow_option_buying:
        reasons.append({"title": "Buy options", "detail": "BUY legs enabled (long premium)."})
    if cfg.risk.allow_option_selling:
        reasons.append({"title": "Sell options", "detail": "SELL legs enabled (short premium, more margin)."})
    if not cfg.risk.allow_option_buying and not cfg.risk.allow_option_selling:
        reasons.append({"title": "No option legs", "detail": "Enable buy and/or sell in Risk controls."})
    ks = kill_switch_state(cfg.risk)
    reasons.append(
        {
            "title": "Kill switch (Live only)",
            "detail": (
                f"In Live mode, stops after {cfg.risk.max_losing_trades_per_day} losing trades or "
                f"₹{cfg.risk.max_daily_loss_rupees:,.0f} daily loss. "
                "Paper trading is not blocked by the kill switch."
            ),
        }
    )
    if ks["active"]:
        reasons.append(
            {
                "title": "Kill switch ACTIVE",
                "detail": " ".join(ks["reasons"]),
            }
        )
    elif ks.get("triggered") and cfg.risk.trading_mode != "LIVE":
        reasons.append(
            {
                "title": "Kill switch (would block Live)",
                "detail": " ".join(ks["reasons"]),
            }
        )
    can_send_live = (
        cfg.dhan.ready
        and cfg.risk.trading_mode == "LIVE"
        and cfg.risk.allow_live_trading
        and not ks["active"]
    )
    return {
        "can_send_live_orders": can_send_live,
        "trading_mode": cfg.risk.trading_mode,
        "allow_live_trading_env": cfg.risk.allow_live_trading,
        "kill_switch": ks,
        "reasons": reasons,
        "how_to_enable_live": [
            "Turn the header switch to Live (requires Dhan Ready)",
            "Ensure kill switch is not active",
        ],
    }


@app.get("/api/status", include_in_schema=False)
async def status() -> dict[str, Any]:
    cfg = settings()
    return {
        "dhan_ready": cfg.dhan.ready,
        "dhan_token": jwt_token_status(cfg.dhan.access_token) if cfg.dhan.access_token else None,
        "dhan_app_credentials_ready": cfg.dhan.app_credentials_ready,
        "dhan_can_generate_consent": cfg.dhan.can_generate_consent,
        "dhan_token_expiry": cfg.dhan.token_expiry,
        "trading_mode": cfg.risk.trading_mode,
        "live_allowed": cfg.risk.allow_live_trading,
        "trading_gates": _trading_gates(cfg),
        "symbols": [item.__dict__ for item in instruments().values()],
        "indices_active": list(configured_index_keys()),
        "indices_unconfigured": list(unconfigured_index_keys()),
        "learned": learned_settings(),
        "auto": scanner_status(),
        "dhan_auth": auth_setup_checklist(settings().dhan),
        "trade_summary": trades_summary(),
        "policy": policy_summary(),
        "strategy": strategy_tuning_summary(),
        "kill_switch": kill_switch_state(cfg.risk),
        "market": market_status(),
        "timezone": "Asia/Kolkata",
    }


@app.get("/api/analytics", include_in_schema=False)
async def analytics() -> dict[str, Any]:
    cfg = settings()
    client = DhanClient(cfg.dhan) if cfg.dhan.ready else None
    return build_analytics(client=client)


@app.get("/api/trades/live-mtm", include_in_schema=False)
async def trades_live_mtm() -> dict[str, Any]:
    """Lightweight poll for open-trade MTM (dashboard refresh)."""
    cfg = settings()
    if not cfg.dhan.ready:
        return {"error": _dhan_setup_message(), "trades": []}
    from index_ai.learning import open_trades
    from index_ai.mtm import enrich_open_trades_mtm

    client = DhanClient(cfg.dhan)
    enriched = enrich_open_trades_mtm(open_trades(), client)
    rows = [format_trade_for_ui(t) for t in enriched]
    open_mtm = sum(float(r["mtm_pnl"]) for r in rows if r.get("mtm_pnl") is not None)
    return {
        "trades": rows,
        "open_mtm_rupees": round(open_mtm, 2),
        "updated_at_ist": format_ist_display(now_ist_iso()),
    }


@app.post("/api/trading/mode", include_in_schema=False)
async def trading_mode(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        mode = set_trading_mode(str(payload.get("mode") or "PAPER"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = settings()
    return {
        "trading_mode": mode,
        "live_orders_enabled": cfg.risk.allow_live_trading,
        "trading_gates": _trading_gates(cfg),
        "kill_switch": kill_switch_state(cfg.risk),
    }


@app.get("/api/auth/setup", include_in_schema=False)
async def auth_setup() -> dict[str, Any]:
    """Which .env fields are set for the Dhan API-key login flow (no secrets returned)."""
    return auth_setup_checklist(settings().dhan)


@app.post("/api/auth/generate-consent", include_in_schema=False)
async def auth_generate_consent() -> dict[str, Any]:
    try:
        return generate_consent(settings().dhan)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/consume-consent", include_in_schema=False)
async def auth_consume_consent(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        reconcile_env_with_jwt()
        result = save_token_from_user_input(settings().dhan, str(payload.get("token_id") or ""))
        clear_auth_block()
        cfg = settings()
        health = check_dhan_health(cfg.dhan)
        return {**result, "health": health, "jwt": jwt_token_status(cfg.dhan.access_token)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.api_route("/api/auth/verify-token", methods=["GET", "POST"], include_in_schema=False)
async def auth_verify_token() -> dict[str, Any]:
    """Confirm DHAN_ACCESS_TOKEN works against Dhan /profile."""
    try:
        return verify_access_token(settings().dhan)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/auth/health", include_in_schema=False)
async def auth_health() -> dict[str, Any]:
    """Profile + data plan + intraday chart probe (diagnoses 401 on heatmap)."""
    return check_dhan_health(settings().dhan)


@app.post("/api/auth/renew-token", include_in_schema=False)
async def auth_renew_token() -> dict[str, Any]:
    try:
        result = renew_access_token(settings().dhan)
        clear_auth_block()
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/heatmap", include_in_schema=False)
async def heatmap() -> dict[str, Any]:
    cfg = settings()
    if not cfg.dhan.ready:
        return {"error": _dhan_setup_message(), "cells": []}
    try:
        return build_heatmap(DhanClient(cfg.dhan), cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/learning", include_in_schema=False)
async def learning_status_api() -> dict[str, Any]:
    return learning_report()


@app.post("/api/learning/hf-sync", include_in_schema=False)
async def learning_hf_sync() -> dict[str, Any]:
    from index_ai.hf_learning import sync_hf_dataset, update_hf_learning

    return {"dataset": sync_hf_dataset(), "hf": update_hf_learning(), "learning": learning_report()}


@app.post("/api/learning/hf-upload", include_in_schema=False)
async def learning_hf_upload() -> dict[str, Any]:
    from index_ai.hf_learning import upload_dataset_to_hub

    return {"upload": upload_dataset_to_hub(), "learning": learning_report()}


@app.post("/api/learning/hf-score", include_in_schema=False)
async def learning_hf_score(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Score a hypothetical setup (debug / preview FinBERT)."""
    from index_ai.hf_learning import score_setup_hf

    return score_setup_hf(
        payload.get("signal") or {},
        payload.get("option"),
        str(payload.get("instrument") or "NIFTY"),
    )


@app.post("/api/learning/retrain-ml", include_in_schema=False)
async def learning_retrain_ml() -> dict[str, Any]:
    """Force retrain the outcome classifier from closed trades in the journal."""
    from index_ai.ml_outcomes import train_outcome_model

    ml = train_outcome_model(force=True)
    from index_ai.learning import learning_report

    return {"ml": ml, "learning": learning_report()}


@app.post("/api/learning/cleanup", include_in_schema=False)
async def learning_cleanup() -> dict[str, Any]:
    """Remove test/automation feedback and recompute confidence gates from real trades only."""
    from index_ai.learning import purge_test_learning_data

    removed = purge_test_learning_data()
    return {"removed": removed, "learning": learning_report()}


@app.post("/api/auto/start", include_in_schema=False)
async def auto_start() -> dict[str, Any]:
    cfg = settings()
    health = check_dhan_health(cfg.dhan) if cfg.dhan.ready else None
    if health and not health.get("charts_ok"):
        detail = "; ".join(health.get("issues") or ["Dhan chart data unavailable."])
        actions = health.get("actions") or []
        if actions:
            detail += " — " + " ".join(actions)
        raise HTTPException(status_code=400, detail=detail)
    try:
        return await start_scanner()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auto/stop", include_in_schema=False)
async def auto_stop() -> dict[str, Any]:
    return await stop_scanner()


@app.get("/api/auto/status", include_in_schema=False)
async def auto_status() -> dict[str, Any]:
    return scanner_status()


@app.post("/api/live-plan", include_in_schema=False)
async def live_plan(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = settings()
    instrument_key = str(payload.get("instrument") or "NIFTY")
    if not cfg.dhan.ready:
        return {"instrument": get_instrument(instrument_key).__dict__, "error": _dhan_setup_message()}
    try:
        return plan_instrument(
            client=DhanClient(cfg.dhan),
            app_settings=cfg,
            instrument_key=instrument_key,
            lookback_days=int(payload.get("lookback_days") or 10),
            interval=str(payload.get("interval") or "5"),
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Dhan API error ({exc.response.status_code}): {(exc.response.text or '')[:400]}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/analyze", include_in_schema=False)
async def analyze(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = settings()
    client = DhanClient(cfg.dhan)
    instrument = get_instrument(str(payload.get("instrument") or "NIFTY"))

    candles = payload.get("candles")
    previous = payload.get("previous_day")
    if not candles or not previous:
        raise RuntimeError("Provide candles and previous_day arrays for signal analysis.")
    today = pd.DataFrame(candles)
    prev = pd.DataFrame(previous)
    from index_ai.strategy import intraday_strategy_signal

    signal = intraday_strategy_signal(today, prev)

    option = None
    expiry = None
    if signal.action != "NO_TRADE" and cfg.dhan.ready:
        expiries = client.expiry_list(instrument)
        expiry = expiries[0] if expiries else None
        if expiry:
            chain = client.option_chain(instrument, expiry)
            option = choose_option_from_chain(chain, signal, instrument)

    plan = build_execution_plan(
        app_settings=cfg,
        instrument=instrument,
        signal=signal,
        option=option,
    )
    return {
        "instrument": instrument.__dict__,
        "signal": signal.to_dict(),
        "expiry": expiry,
        "option": option,
        "plan": plan.__dict__,
    }


@app.post("/api/execute", include_in_schema=False)
async def execute(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = settings()
    client = DhanClient(cfg.dhan)
    instrument = get_instrument(str(payload.get("instrument") or "NIFTY"))
    signal_payload = payload.get("signal") or {}
    from index_ai.strategy import StrategySignal

    signal = StrategySignal(**signal_payload)
    option = payload.get("option")
    if option and payload.get("transaction_type"):
        option = {**option, "transaction_type": str(payload["transaction_type"]).upper()}
    plan = build_execution_plan(
        app_settings=cfg,
        instrument=instrument,
        signal=signal,
        option=option,
    )
    return execute_plan(plan, cfg, client)


@app.post("/api/trades/check-trails", include_in_schema=False)
async def check_trailing_stops(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Update trailing stops on open trades using latest index price from Dhan."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    client = DhanClient(cfg.dhan)
    results: list[dict[str, Any]] = []
    for trade in open_trades():
        instrument_key = str(trade.get("instrument") or payload.get("instrument") or "NIFTY")
        if payload.get("instrument") and instrument_key != payload.get("instrument"):
            continue
        inst = get_instrument(instrument_key)
        quote = client.index_ltp(inst)
        price = float(quote.get("last_price") or quote.get("ltp") or trade["signal"]["price"])
        fresh_st = fetch_supertrend_snapshot(client, instrument_key)
        evaluation = evaluate_open_trade(
            trade, price, cfg.risk, fresh_supertrend=fresh_st
        )
        update_trade_trail_meta(str(trade["id"]), evaluation["trail"])
        closed = None
        if evaluation.get("should_exit"):
            closed = close_open_trade(
                trade,
                client=client,
                app_settings=cfg,
                reason=str(evaluation.get("exit_reason") or "Trailing stop"),
                index_price=price,
            )
        results.append({**evaluation, "closed": closed})
    return {
        "checks": results,
        "kill_switch": kill_switch_state(cfg.risk),
        "market": market_status(),
    }


@app.get("/api/trades", include_in_schema=False)
async def trades(limit: int = 50) -> dict[str, Any]:
    raw = recent_trades(limit=max(1, min(limit, 200)))
    return {
        "trades": raw,
        "rows": [format_trade_for_ui(t) for t in raw],
        "summary": trades_summary(),
        "learned": learned_settings(),
        "auto": scanner_status(),
    }


@app.post("/api/feedback", include_in_schema=False)
async def feedback(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    learned = record_feedback(
        trade_id=payload.get("trade_id"),
        rating=int(payload.get("rating") or 0),
        note=payload.get("note"),
    )
    return {"learned": learned}


@app.post("/api/outcome", include_in_schema=False)
async def outcome(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    learned = record_trade_outcome(
        trade_id=str(payload.get("trade_id") or ""),
        pnl=float(payload.get("pnl") or 0),
        note=payload.get("note"),
    )
    return {"learned": learned}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse("/favicon.svg")


if DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


def run() -> None:
    uvicorn.run("index_ai.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
