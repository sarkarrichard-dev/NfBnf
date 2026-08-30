from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

from index_ai.analytics import build_analytics
from index_ai.reports import build_report, export_filename, report_to_csv
from index_ai.config import (
    ARM_LIVE_PHRASE,
    DASHBOARD_DIR,
    MEMORY_DIR,
    arm_live_trading,
    candle_interval_minutes,
    disarm_live_trading,
    feature_flags,
    set_feature_flag,
    set_trading_mode,
    settings,
)
from index_ai.risk import kill_switch_state
from index_ai.risk_policy import policy_summary
from index_ai.strategies.strategy_params import strategy_tuning_summary
from index_ai.dhan import DhanClient
from index_ai.dhan_auth import (
    auto_refresh_dhan_token,
    auth_setup_checklist,
    check_dhan_health,
    generate_access_token_via_totp,
    generate_consent,
    jwt_token_status,
    oauth_redirect_urls,
    parse_oauth_callback_value,
    reconcile_env_with_jwt,
    renew_access_token,
    save_token_from_user_input,
    token_renew_status,
    verify_access_token,
)
from index_ai.executor import build_execution_plan, execute_plan
from index_ai.instruments import (
    configured_index_keys,
    get_instrument,
    instruments,
    unconfigured_index_keys,
)
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
    update_learning,
    update_trade_trail_meta,
)
from index_ai.chart_live import fetch_supertrend_snapshot
from index_ai.trailing import evaluate_open_trade
from index_ai.heatmap import build_heatmap
from index_ai.planner import plan_instrument
from index_ai.market_clock import format_ist_display, market_status, now_ist_iso
from index_ai.scanner import (
    bootstrap_scanner,
    clear_auth_block,
    queue_bootstrap_scanner,
    schedule_boot_auto_start,
    scanner_status,
    stop_scanner,
)
from index_ai.trade_lots import adjust_lots_per_trade, lots_settings_summary, set_lots_per_trade
from index_ai.exit import close_open_trade


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from index_ai.single_instance import acquire_or_exit

    acquire_or_exit()  # a second instance sharing this .env + DB is the switch-lag cause

    from index_ai.learning import reconcile_all_trade_lots

    async def _auto_renew_loop() -> None:
        from index_ai.dhan_auth import _auto_renew_poll_seconds, _auto_renew_enabled

        while True:
            await asyncio.sleep(_auto_renew_poll_seconds())
            if not _auto_renew_enabled():
                continue
            cfg = settings()
            if cfg.dhan.ready:
                auto_refresh_dhan_token(cfg.dhan, reason="background_poll")

    init_db()
    reconcile_all_trade_lots()
    from index_ai.learning import repair_closed_trade_prices
    from index_ai.strategies.strategy_params import get_strategy_params, reload_strategy_params

    reload_strategy_params()
    sp = get_strategy_params()
    logging.getLogger(__name__).info(
        "Strategy boot: style=%s intelligent_routing=%s loss_guard=%s",
        os.getenv("STRATEGY_STYLE", "AUTO"),
        sp.auto_intelligent_routing,
        os.getenv("LOSS_GUARD_ENABLED", "true"),
    )

    repair_closed_trade_prices()
    cfg = settings()
    from index_ai.dhan_auth import jwt_token_status, totp_credentials_configured

    if cfg.dhan.ready:
        jwt = jwt_token_status(cfg.dhan.access_token) if cfg.dhan.access_token else {}
        if jwt.get("expired") and totp_credentials_configured():
            auto_refresh_dhan_token(cfg.dhan, force=True, reason="startup_expired_totp")
        else:
            auto_refresh_dhan_token(cfg.dhan, reason="startup")
    elif totp_credentials_configured():
        auto_refresh_dhan_token(cfg.dhan, force=True, reason="startup_totp")

    async def _candle_cache_loop() -> None:
        from index_ai.candle_cache import sync_all_configured
        from index_ai.dhan import DhanClient
        from index_ai.market_clock import is_market_open

        while True:
            await asyncio.sleep(1800)
            if not is_market_open():
                continue
            cfg = settings()
            if not cfg.dhan.ready:
                continue
            try:
                sync_all_configured(DhanClient(cfg.dhan), interval=candle_interval_minutes())
            except Exception:
                pass

    boot_scanner_task = asyncio.create_task(schedule_boot_auto_start())
    renew_task = asyncio.create_task(_auto_renew_loop())
    cache_task = asyncio.create_task(_candle_cache_loop())

    async def _tick_feed_loop() -> None:
        """Live websocket tick stream — opt-in, reconnects itself, never fatal."""
        from index_ai.tick_feed import enabled as tick_enabled, run_feed

        if not tick_enabled():
            return
        while True:
            c = settings()
            if c.dhan.ready and c.dhan.access_token and c.dhan.client_id:
                try:
                    await run_feed(c.dhan.access_token, str(c.dhan.client_id), stop=tick_stop)
                except Exception:
                    logging.getLogger(__name__).warning("tick feed loop error", exc_info=True)
            if tick_stop.is_set():
                return
            await asyncio.sleep(30)

    tick_stop = asyncio.Event()
    tick_task = asyncio.create_task(_tick_feed_loop())

    async def _warm() -> None:
        """Spin up the thread pool and touch the modules the first UI action needs.

        Without this the first mode switch or lot change pays ~2.5s of pool
        startup and lazy imports, which reads as a hung button on a fresh server.
        """

        def _touch() -> None:
            from index_ai.learning import open_trades_for_mode
            from index_ai.trade_lots import lots_settings_summary

            settings()
            lots_settings_summary()
            open_trades_for_mode("PAPER")

        try:
            await asyncio.to_thread(_touch)
        except Exception:
            pass

    warm_task = asyncio.create_task(_warm())
    if cfg.dhan.ready:
        try:
            from index_ai.candle_cache import ensure_active_interval_cache, sync_all_configured
            from index_ai.dhan import DhanClient

            dhan_client = DhanClient(cfg.dhan)
            ensure_active_interval_cache(dhan_client)
            sync_all_configured(dhan_client, interval=candle_interval_minutes())
        except Exception:
            pass
    yield
    tick_stop.set()
    renew_task.cancel()
    boot_scanner_task.cancel()
    cache_task.cancel()
    tick_task.cancel()
    warm_task.cancel()
    for task in (renew_task, boot_scanner_task, cache_task, tick_task, warm_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await stop_scanner()


app = FastAPI(title="Index Options AI", version="0.2.0", lifespan=lifespan)

# Exposed on /api/status so the dashboard can detect a stale server process.
API_CAPABILITIES: dict[str, Any] = {
    "backtest_dhan": True,
    "candle_cache": True,
    "build": "2026-06-07",
}
HEALTH_APP_ID = "index-options-ai"
API_FEATURES = (
    "learning_optimize",
    "oi_heatmap",
    "profit_trail",
    "trade_lots",
    "live_polling",
    "dhan_account",
)


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, Any]:
    """Lightweight probe so the dashboard can detect the correct server on port 8000."""
    return {
        "ok": True,
        "app": HEALTH_APP_ID,
        "version": app.version,
        "features": list(API_FEATURES),
    }


def _oauth_token_from_request(request: Request, token_id: str | None) -> str | None:
    for key in ("tokenId", "tokenid", "token_id"):
        raw = request.query_params.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    if token_id and str(token_id).strip():
        return str(token_id).strip()
    return None


def _dhan_oauth_redirect(request: Request, token_id: str | None) -> RedirectResponse:
    raw = _oauth_token_from_request(request, token_id)
    parsed = parse_oauth_callback_value(raw)
    if not parsed:
        detail = quote(
            "Missing tokenId in redirect URL. Register redirect URL on Dhan as: "
            + oauth_redirect_urls()[0]
        )
        return RedirectResponse(url=f"/?dhan_auth=error&detail={detail}", status_code=302)
    try:
        reconcile_env_with_jwt()
        save_token_from_user_input(settings().dhan, parsed)
        clear_auth_block()
        queue_bootstrap_scanner()
        return RedirectResponse(url="/?dhan_auth=success", status_code=302)
    except Exception as exc:
        detail = quote(str(exc)[:500])
        return RedirectResponse(url=f"/?dhan_auth=error&detail={detail}", status_code=302)


@app.get("/dhan/oauth/callback", include_in_schema=False)
async def dhan_oauth_callback(
    request: Request,
    token_id: str | None = Query(None, alias="tokenId"),
) -> RedirectResponse:
    """Dhan OAuth redirect target — exchanges tokenId and opens the dashboard."""
    return _dhan_oauth_redirect(request, token_id)


@app.get("/callback", include_in_schema=False)
async def dhan_oauth_callback_short(
    request: Request,
    token_id: str | None = Query(None, alias="tokenId"),
) -> RedirectResponse:
    """Alias when redirect URL is http://127.0.0.1:8000/callback"""
    return _dhan_oauth_redirect(request, token_id)


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
                    "ALLOW_LIVE_TRADING=false — no broker orders are sent. Switching to Live "
                    "arms real orders only after you confirm in the dialog. Switching back to "
                    "Paper always disarms."
                ),
            }
        )
    if cfg.risk.allow_option_buying:
        reasons.append({"title": "Buy options", "detail": "BUY legs enabled (long premium)."})
    if cfg.risk.allow_option_selling:
        reasons.append(
            {"title": "Sell options", "detail": "SELL legs enabled (short premium, more margin)."}
        )
    if not cfg.risk.allow_option_buying and not cfg.risk.allow_option_selling:
        reasons.append(
            {"title": "No option legs", "detail": "Enable buy and/or sell in Risk controls."}
        )
    ks = kill_switch_state(cfg.risk)
    reasons.append(
        {
            "title": "Kill switch (Live only)",
            "detail": (
                f"In Live mode, stops after {cfg.risk.max_losing_trades_per_day} consecutive losing "
                f"trades or ₹{cfg.risk.max_daily_loss_rupees:,.0f} daily loss "
                f"(₹6,000 × lots per trade). Paper is not blocked."
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
    from index_ai.dhan_network import dhan_order_ip_whitelist_hint, fetch_public_ip

    ip_hint = dhan_order_ip_whitelist_hint(fetch_public_ip())
    return {
        "can_send_live_orders": can_send_live,
        "dhan_order_ip_whitelist": ip_hint,
        "trading_mode": cfg.risk.trading_mode,
        "allow_live_trading_env": cfg.risk.allow_live_trading,
        "kill_switch": ks,
        "reasons": reasons,
        "how_to_enable_live": [
            "Turn the header switch to Live (requires Dhan Ready)",
            "Ensure kill switch is not active",
        ],
    }


@app.get("/api/ops/status", include_in_schema=False)
def ops_status() -> dict[str, Any]:  # sync
    from index_ai.ops_status import build_ops_status

    return build_ops_status()


@app.get("/api/ops/scan-preview", include_in_schema=False)
async def ops_scan_preview(instrument: str = Query("NIFTY")) -> dict[str, Any]:
    from index_ai.ops_status import preview_scan

    return preview_scan(instrument)


@app.get("/api/status", include_in_schema=False)
async def status() -> dict[str, Any]:
    # ~250ms of synchronous SQLite + checklist work. Off the event loop, or every
    # concurrent dashboard poll queues behind it and the whole UI stutters.
    return await asyncio.to_thread(_status_payload)


def _status_payload() -> dict[str, Any]:
    cfg = settings()
    return {
        "dhan_ready": cfg.dhan.ready,
        "dhan_token": jwt_token_status(cfg.dhan.access_token) if cfg.dhan.access_token else None,
        "dhan_app_credentials_ready": cfg.dhan.app_credentials_ready,
        "dhan_can_generate_consent": cfg.dhan.can_generate_consent,
        "dhan_token_expiry": format_ist_display(cfg.dhan.token_expiry)
        if cfg.dhan.token_expiry
        else None,
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
        "dhan_health": check_dhan_health(cfg.dhan) if cfg.dhan.ready else None,
        "token_renew": token_renew_status(),
        "dhan_account_hint": (
            "GET /api/dhan/account for live funds and trade book" if cfg.dhan.ready else None
        ),
        "api_capabilities": API_CAPABILITIES,
    }


@app.get("/api/analytics", include_in_schema=False)
def analytics(  # sync: SQLite + pandas (+ Dhan when enrich_mtm) — Starlette threadpools it
    enrich_mtm: bool = Query(True, description="Fetch live LTP for open legs"),
) -> dict[str, Any]:
    cfg = settings()
    client = DhanClient(cfg.dhan) if cfg.dhan.ready and enrich_mtm else None
    return build_analytics(client=client, enrich_mtm=enrich_mtm)


@app.get("/api/trades/cleanup/preview", include_in_schema=False)
async def trades_cleanup_preview() -> dict[str, Any]:
    from index_ai.trade_cleanup import scan_trade_cleanup

    return scan_trade_cleanup()


@app.post("/api/trades/cleanup", include_in_schema=False)
async def trades_cleanup(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    from index_ai.trade_cleanup import run_trade_cleanup

    dry_run = bool(payload.get("dry_run"))
    ids = payload.get("trade_ids")
    trade_ids = [str(x) for x in ids] if isinstance(ids, list) else None
    if not dry_run and not payload.get("confirm"):
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to delete trades.",
        )
    if trade_ids:
        return run_trade_cleanup(trade_ids=trade_ids, dry_run=dry_run)
    return run_trade_cleanup(dry_run=dry_run)


@app.get("/api/trades/recent", include_in_schema=False)
def trades_recent(limit: int = Query(80, ge=1, le=200)) -> dict[str, Any]:  # sync SQLite
    """Fast journal poll — no Dhan LTP calls (use /api/trades/live-mtm for open MTM)."""
    from index_ai.learning import expand_trades_to_log_rows, repair_rejected_journal_prices

    repair_rejected_journal_prices()
    raw = recent_trades(limit=limit)
    ui_rows = [format_trade_for_ui(t) for t in raw]
    return {
        "rows": ui_rows,
        "log_rows": expand_trades_to_log_rows(ui_rows),
        "count": len(raw),
        "updated_at_ist": format_ist_display(now_ist_iso()),
    }


@app.get("/api/reports/export", include_in_schema=False)
async def export_report(
    period: str = Query("today", description="today | week | month | all | custom"),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> Response:
    """Download CSV: PnL summary + order rows for the selected IST period."""
    cfg = settings()
    client = DhanClient(cfg.dhan) if cfg.dhan.ready else None
    try:
        report = build_report(
            period,
            from_date=from_date,
            to_date=to_date,
            client=client,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    csv_text = report_to_csv(report)
    filename = export_filename(report)
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/trades/live-mtm", include_in_schema=False)
def trades_live_mtm(sync_broker: bool = Query(False)) -> dict[str, Any]:  # sync Dhan + SQLite
    """Fast MTM poll for open trades (paper + live). Use sync_broker=true only occasionally."""
    cfg = settings()
    if not cfg.dhan.ready:
        return {"error": _dhan_setup_message(), "trades": []}
    from index_ai.dhan_orders import sync_open_live_trades
    from index_ai.learning import expand_trades_to_log_rows
    from index_ai.mtm import enrich_open_trades_mtm

    client = DhanClient(cfg.dhan)
    if sync_broker:
        sync_open_live_trades(client)
    enriched = enrich_open_trades_mtm(open_trades(), client, persist=False)
    rows = [format_trade_for_ui(t) for t in enriched]
    open_mtm = sum(float(r["mtm_pnl"]) for r in rows if r.get("mtm_pnl") is not None)
    return {
        "trades": rows,
        "log_rows": expand_trades_to_log_rows(rows),
        "open_mtm_rupees": round(open_mtm, 2),
        "updated_at_ist": format_ist_display(now_ist_iso()),
    }


@app.get("/api/dhan/account", include_in_schema=False)
def dhan_account_snapshot(sync_broker: bool = Query(False)) -> dict[str, Any]:  # sync Dhan calls
    """Live Dhan portal data: fund limits, today's trade book, open positions."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.dhan import DhanClient
    from index_ai.dhan_portfolio import fetch_dhan_account_snapshot

    client = DhanClient(cfg.dhan)
    from index_ai.dhan_orders import sync_open_live_trades
    from index_ai.learning import open_trades_for_mode

    journal_synced = sync_open_live_trades(client) if sync_broker else 0
    snapshot = fetch_dhan_account_snapshot(client)
    snapshot["journal_sync_updated"] = journal_synced
    live_open = len(open_trades_for_mode("LIVE"))
    if snapshot.get("positions_count") and not snapshot.get("tradebook_count"):
        snapshot["positions_note"] = (
            "Positions include carryforward from prior sessions. "
            "Trade book below is today’s fills only — empty means no algo fills today."
        )
    if live_open and snapshot.get("positions_count") == 0:
        snapshot["journal_broker_mismatch"] = (
            f"Journal has {live_open} open live row(s) but Dhan reports no net positions — "
            "sync marked phantoms rejected; refresh analytics."
        )
    elif not live_open and snapshot.get("positions_count"):
        snapshot["journal_broker_mismatch"] = (
            "Dhan shows open positions not tied to an open algo journal spread — "
            "often orphan legs from a partial live entry. Square off on Dhan if unintended."
        )
    if snapshot.get("errors") and not snapshot.get("ok"):
        snapshot["error"] = "; ".join(snapshot["errors"])
    return snapshot


@app.get("/api/dhan/funds", include_in_schema=False)
def dhan_funds() -> dict[str, Any]:  # sync Dhan call
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.dhan import DhanClient
    from index_ai.dhan_portfolio import normalize_fund_limits
    from index_ai.market_clock import format_ist_display, now_ist_iso

    client = DhanClient(cfg.dhan)
    funds = normalize_fund_limits(client.get_fund_limits())
    return {
        "funds": funds,
        "updated_at_ist": format_ist_display(now_ist_iso()),
    }


@app.get("/api/dhan/tradebook", include_in_schema=False)
def dhan_tradebook() -> dict[str, Any]:  # sync Dhan call
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.dhan import DhanClient
    from index_ai.dhan_portfolio import format_trade_book_row
    from index_ai.market_clock import format_ist_display, now_ist_iso

    client = DhanClient(cfg.dhan)
    rows = [format_trade_book_row(r) for r in client.list_today_trades()]
    rows.sort(
        key=lambda r: str(r.get("exchange_time") or r.get("create_time") or ""),
        reverse=True,
    )
    return {
        "trades": rows,
        "count": len(rows),
        "updated_at_ist": format_ist_display(now_ist_iso()),
    }


@app.post("/api/trades/sync-broker", include_in_schema=False)
async def sync_broker_orders() -> dict[str, Any]:
    """Poll Dhan order book and update LIVE_SENT / rejected journal rows."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.dhan_orders import sync_open_live_trades
    from index_ai.learning import live_trades_for_broker_sync, recent_trades

    client = DhanClient(cfg.dhan)
    updated = sync_open_live_trades(client)
    rows = [format_trade_for_ui(t) for t in recent_trades(limit=80) if is_live_trade_ui(t)]
    return {
        "updated": updated,
        "live_trades": rows,
        "pending_sync": len(live_trades_for_broker_sync()),
    }


def is_live_trade_ui(trade: dict[str, Any]) -> bool:
    from index_ai.learning import is_live_trade

    return is_live_trade(trade)


@app.get("/api/settings/lots", include_in_schema=False)
async def get_lots_settings() -> dict[str, Any]:
    return lots_settings_summary()


@app.post("/api/settings/lots", include_in_schema=False)
async def update_lots_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Set lots per trade (1–10) or adjust with delta (+1 / -1). Re-syncs open journal quantities."""
    from index_ai.learning import reconcile_all_trade_lots

    raw = payload.get("lots")
    if "delta" not in payload and raw is None:
        raise HTTPException(status_code=400, detail="Provide lots (integer) or delta (+1 / -1).")

    def _apply() -> dict[str, Any]:
        # .env write + SQLite, both blocking — off the event loop or every
        # concurrent dashboard poll queues behind it and the whole UI stalls.
        if "delta" in payload:
            out = adjust_lots_per_trade(int(payload.get("delta") or 0))
        else:
            out = set_lots_per_trade(int(raw))
        # only OPEN rows: a closed trade's quantity records what was actually
        # traded, so rewriting it would falsify the journal
        out["reconcile"] = reconcile_all_trade_lots(open_only=True)
        out["policy"] = policy_summary()
        return out  # carries lots_per_trade so the client reconciles to the truth

    return await asyncio.to_thread(_apply)


@app.post("/api/trading/mode", include_in_schema=False)
async def trading_mode(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    from index_ai.learning import open_trades_for_mode

    def _switch() -> tuple[str, Any, int, int]:
        # set_trading_mode writes .env and settings() re-reads it — both blocking.
        m = set_trading_mode(str(payload.get("mode") or "PAPER"))
        c = settings()
        return m, c, len(open_trades_for_mode("PAPER")), len(open_trades_for_mode("LIVE"))

    try:
        mode, cfg, paper_open, live_open = await asyncio.to_thread(_switch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "trading_mode": mode,
        "live_orders_enabled": cfg.risk.allow_live_trading,
        "trading_gates": _trading_gates(cfg),
        "kill_switch": kill_switch_state(cfg.risk),
        "open_trades_paper": paper_open,
        "open_trades_live": live_open,
        "note": (
            f"{paper_open} paper open position(s) do not block live scanner entries."
            if mode == "LIVE" and paper_open
            else None
        ),
    }


@app.get("/api/auth/setup", include_in_schema=False)
async def auth_setup() -> dict[str, Any]:
    """Which .env fields are set for the Dhan API-key login flow (no secrets returned)."""
    from index_ai.dhan_network import dhan_order_ip_whitelist_hint, fetch_public_ip

    out = auth_setup_checklist(settings().dhan)
    out["order_ip_whitelist"] = dhan_order_ip_whitelist_hint(fetch_public_ip())
    return out


@app.post("/api/auth/generate-consent", include_in_schema=False)
async def auth_generate_consent() -> dict[str, Any]:
    try:
        return generate_consent(settings().dhan)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/consume-consent", include_in_schema=False)
async def auth_consume_consent(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        reconcile_env_with_jwt()
        result = save_token_from_user_input(settings().dhan, str(payload.get("token_id") or ""))
        clear_auth_block()
        queue_bootstrap_scanner()
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


@app.api_route("/api/auth/health", methods=["GET", "POST"], include_in_schema=False)
async def auth_health(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Profile + data plan + chart probe. POST optional token_id saves pasted token first."""
    token_raw = str(payload.get("token_id") or payload.get("token") or "").strip()
    if token_raw:
        try:
            save_token_from_user_input(settings().dhan, token_raw)
            clear_auth_block()
            queue_bootstrap_scanner()
        except Exception as exc:
            return {
                "ok": False,
                "charts_ok": False,
                "issues": [f"Could not save pasted token: {exc}"],
                "actions": [
                    "Paste the full eyJ… JWT from Dhan Web (no spaces or line breaks), then Verify again.",
                ],
            }
    return check_dhan_health(settings().dhan)


@app.post("/api/auth/renew-token", include_in_schema=False)
async def auth_renew_token() -> dict[str, Any]:
    cfg = settings()
    try:
        result = renew_access_token(cfg.dhan)
    except Exception as renew_exc:
        from index_ai.dhan_auth import totp_credentials_configured

        if not totp_credentials_configured():
            raise HTTPException(status_code=400, detail=str(renew_exc)) from renew_exc
        try:
            result = generate_access_token_via_totp(cfg.dhan)
        except Exception as totp_exc:
            raise HTTPException(
                status_code=400,
                detail=f"{renew_exc} TOTP fallback failed: {totp_exc}",
            ) from totp_exc
    clear_auth_block()
    queue_bootstrap_scanner()
    cfg = settings()
    return {
        **result,
        "health": check_dhan_health(cfg.dhan),
        "jwt": jwt_token_status(cfg.dhan.access_token),
    }


@app.post("/api/auth/totp-login", include_in_schema=False)
async def auth_totp_login() -> dict[str, Any]:
    """Mint a fresh WEB access token using DHAN_PIN + DHAN_TOTP_SECRET (no browser)."""
    try:
        result = generate_access_token_via_totp(settings().dhan)
        clear_auth_block()
        queue_bootstrap_scanner()
        cfg = settings()
        return {
            **result,
            "health": check_dhan_health(cfg.dhan),
            "jwt": jwt_token_status(cfg.dhan.access_token),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/research/ping", include_in_schema=False)
async def research_ping() -> dict[str, Any]:
    """Lightweight probe — dashboard uses this to detect a stale server process."""
    return {"ok": True, "capabilities": API_CAPABILITIES}


@app.post("/api/research/backtest-dhan", include_in_schema=False)
async def research_backtest_dhan(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """
    Replay strategy on cached / Dhan intraday candles. Cache enables lookbacks > 5 days.

    pnl_mode: option_proxy (default) | spot
    """
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    health = check_dhan_health(cfg.dhan, use_cache=True)
    if not health.get("charts_ok"):
        raise HTTPException(
            status_code=400,
            detail="Dhan Data API / intraday charts required for backtest. "
            + "; ".join(
                health.get("actions") or health.get("issues") or ["Enable Data API on Dhan Web."]
            ),
        )
    instrument = str(payload.get("instrument") or payload.get("index") or "NIFTY").strip().upper()
    lookback = int(payload.get("days") or payload.get("lookback_days") or 5)
    interval = str(payload.get("interval") or candle_interval_minutes())
    use_cache = str(payload.get("use_cache", "true")).strip().lower() not in {"0", "false", "no"}
    refresh_cache = str(payload.get("refresh_cache", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    pnl_mode = str(payload.get("pnl_mode") or "option_proxy")
    from index_ai.backtest import run_dhan_intraday_backtest

    client = DhanClient(cfg.dhan)
    try:
        return run_dhan_intraday_backtest(
            client,
            cfg,
            instrument_key=instrument,
            lookback_days=lookback,
            interval=interval,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
            pnl_mode=pnl_mode,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/research/sync-candle-cache", include_in_schema=False)
async def research_sync_candle_cache() -> dict[str, Any]:
    """Pull latest Dhan intraday window into memory/candles for all configured indices."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.backtest import sync_candle_cache

    client = DhanClient(cfg.dhan)
    try:
        return sync_candle_cache(client)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/research/candle-cache", include_in_schema=False)
async def research_candle_cache_status() -> dict[str, Any]:
    from index_ai.candle_cache import cache_status

    return cache_status()


@app.get("/api/heatmap", include_in_schema=False)
def heatmap() -> dict[str, Any]:  # sync: 6+ blocking Dhan calls — Starlette threadpools it
    cfg = settings()
    if not cfg.dhan.ready:
        return {"error": _dhan_setup_message(), "cells": []}
    try:
        return build_heatmap(DhanClient(cfg.dhan), cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/learning", include_in_schema=False)
def learning_status_api() -> dict[str, Any]:  # sync SQLite
    return learning_report()


@app.get("/api/futures/status", include_in_schema=False)
def futures_paper_status_api() -> dict[str, Any]:  # sync
    from index_ai.strategies.futures.paper import futures_paper_status

    return futures_paper_status()


@app.get("/api/options-cpr/status", include_in_schema=False)
def options_cpr_paper_status_api() -> dict[str, Any]:  # sync
    from index_ai.strategies.options_cpr.paper import options_cpr_paper_status

    return options_cpr_paper_status()


@app.get("/api/reconcile", include_in_schema=False)
async def reconcile_api(repair: bool = Query(False)) -> dict[str, Any]:
    """Broker-vs-journal drift check. Read-only unless repair=true (journal only)."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    from index_ai.reconcile import reconcile

    client = DhanClient(cfg.dhan)
    return await asyncio.to_thread(
        reconcile, client, mode=cfg.risk.trading_mode, repair=repair or None
    )


@app.get("/api/settings/features", include_in_schema=False)
async def get_features() -> dict[str, Any]:
    """Feature flags the app can toggle for itself — no .env editing needed."""
    return {"flags": feature_flags()}


@app.post("/api/settings/features", include_in_schema=False)
async def update_feature(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    flag, on = str(payload.get("flag") or ""), bool(payload.get("enabled"))
    try:
        result = await asyncio.to_thread(set_feature_flag, flag, on)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["restart_required"] = flag.upper() == "ENABLE_TICK_FEED"
    result["flags"] = feature_flags()
    return result


@app.post("/api/trading/arm-live", include_in_schema=False)
async def arm_live(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Arm or disarm real broker orders.

    Arming needs the exact confirmation phrase; disarming never does — the safe
    direction should always be one click.
    """
    if payload.get("disarm"):
        await asyncio.to_thread(disarm_live_trading)
    else:
        try:
            await asyncio.to_thread(arm_live_trading, str(payload.get("confirm") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = settings()
    return {
        "live_orders_enabled": cfg.risk.allow_live_trading,
        "trading_mode": cfg.risk.trading_mode,
        "confirm_phrase": ARM_LIVE_PHRASE,
        "trading_gates": _trading_gates(cfg),
    }


@app.get("/api/tick-feed", include_in_schema=False)
async def tick_feed_api() -> dict[str, Any]:
    """Live websocket feed health: connected, ticks seen, stall detection."""
    from index_ai.tick_feed import status

    return status()


@app.get("/api/market-log", include_in_schema=False)
async def market_log_api(
    session: str | None = Query(None),
    instrument: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Time-series of what the system saw, plus why lanes did or didn't trade."""
    from index_ai.market_log import observations, skip_reasons, stats

    return {
        "stats": stats(),
        "observations": observations(session=session, instrument=instrument, limit=limit),
        "top_skip_reasons": skip_reasons(session=session),
    }


@app.get("/api/daily-report", include_in_schema=False)
async def daily_report_api(run: bool = Query(False)) -> dict[str, Any]:
    """Latest end-of-day report. run=true regenerates it now instead of waiting."""
    from index_ai.daily_ops import latest_report, run_eod

    if run:
        return await asyncio.to_thread(run_eod)
    return latest_report() or {"error": "no report yet — generated after square-off each session"}


@app.get("/api/market-context", include_in_schema=False)
async def market_context_api(refresh: bool = Query(False)) -> dict[str, Any]:
    """FII/DII/Pro/Client positioning, India VIX, IV term structure, OI walls, pinning."""
    from index_ai.market_context import context as mkt

    return await asyncio.to_thread(mkt.load_for_session, refresh=refresh)


@app.get("/api/market-context/spreads", include_in_schema=False)
def spread_calibration_api() -> dict[str, Any]:  # sync
    """Observed option bid-ask half-spread per index vs the assumed default."""
    from index_ai.market_context.spread_calib import status

    return status()


@app.get("/api/brain/status", include_in_schema=False)
def brain_status_api() -> dict[str, Any]:  # sync
    """Unified ML brain: dataset size, walk-forward verdict, whether the gate is armed."""
    from index_ai.brain.gate import status

    return status()


@app.get("/api/brain/commentary", include_in_schema=False)
async def brain_commentary_api(
    kind: str = Query("pre_open"), refresh: bool = Query(False)
) -> dict[str, Any]:
    """Advisory-only AI commentary. Never gates or places a trade."""
    from index_ai.brain.commentary import generate, latest

    if not refresh:
        cached = latest(kind)
        if cached:
            return cached
    return await asyncio.to_thread(generate, kind)


@app.post("/api/brain/train", include_in_schema=False)
async def brain_train_api(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Retrain on every lane's closed trades. The gate arms only if walk-forward earns it."""
    from index_ai.brain.model import train

    return await asyncio.to_thread(
        train,
        include_backtest=payload.get("include_backtest"),
        force=bool(payload.get("force")),
    )


@app.api_route("/api/learning/optimize", methods=["GET", "POST"], include_in_schema=False)
async def learning_optimize_api() -> dict[str, Any]:
    """Recompute learning + OI/strategy insights from closed trades and journal."""
    from index_ai.oi_learning import analyze_oi_outcomes

    learned = update_learning()
    return {
        "learned": learned,
        "oi_insights": analyze_oi_outcomes(),
        "strategy_tuning": strategy_tuning_summary(),
        "message": "Learning and OI insights refreshed from trade history.",
    }


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
async def auto_start(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    token_raw = str(payload.get("token_id") or payload.get("token") or "").strip()
    if token_raw:
        try:
            save_token_from_user_input(settings().dhan, token_raw)
            clear_auth_block()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not save token: {exc}") from exc
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    health = check_dhan_health(cfg.dhan)
    if not health.get("token_ok"):
        detail = "; ".join(health.get("issues") or ["Dhan token not accepted."])
        actions = health.get("actions") or []
        if actions:
            detail += " — " + " ".join(actions[:2])
        raise HTTPException(status_code=400, detail=detail)
    if not health.get("charts_ok"):
        detail = "; ".join(health.get("issues") or ["Intraday chart data unavailable."])
        actions = health.get("actions") or []
        if actions:
            detail += " — " + " ".join(actions[:2])
        raise HTTPException(status_code=400, detail=detail)
    result = await bootstrap_scanner(respect_disable_flag=False)
    if not result.get("started"):
        detail = str(result.get("message") or result.get("reason") or "Could not start scanner.")
        raise HTTPException(status_code=400, detail=detail)
    return result.get("status") or scanner_status()


@app.post("/api/auto/stop", include_in_schema=False)
async def auto_stop() -> dict[str, Any]:
    return await stop_scanner()


@app.get("/api/auto/status", include_in_schema=False)
def auto_status() -> dict[str, Any]:  # polled every 3s — keep it off the loop
    return scanner_status()


@app.post("/api/live-plan", include_in_schema=False)
async def live_plan(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg = settings()
    instrument_key = str(payload.get("instrument") or "NIFTY")
    if not cfg.dhan.ready:
        return {
            "instrument": get_instrument(instrument_key).__dict__,
            "error": _dhan_setup_message(),
        }
    try:
        return plan_instrument(
            client=DhanClient(cfg.dhan),
            app_settings=cfg,
            instrument_key=instrument_key,
            lookback_days=int(payload.get("lookback_days") or 10),
            interval=str(payload.get("interval") or candle_interval_minutes()),
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
    from index_ai.strategies.strategy import choose_option_from_chain, intraday_strategy_signal

    signal = intraday_strategy_signal(today, prev)

    option = None
    expiry = None
    if signal.action != "NO_TRADE" and cfg.dhan.ready:
        from index_ai.options_expiry import pick_nearest_expiry

        expiries = client.expiry_list(instrument)
        expiry = pick_nearest_expiry(expiries) if expiries else None
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
    from index_ai.market_clock import is_entry_session_timestamp, now_ist, trading_window_message

    if not is_entry_session_timestamp(now_ist()):
        return {
            "status": "BLOCKED",
            "reason": f"Entries blocked — {trading_window_message(now_ist())}",
            "safety_code": "market_closed",
        }
    cfg = settings()
    client = DhanClient(cfg.dhan)
    instrument = get_instrument(str(payload.get("instrument") or "NIFTY"))
    signal_payload = payload.get("signal") or {}
    from index_ai.execution_safety import signal_from_payload

    signal = signal_from_payload(signal_payload)
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
async def check_trailing_stops(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Update trailing stops on open trades using latest index price from Dhan."""
    cfg = settings()
    if not cfg.dhan.ready:
        raise HTTPException(status_code=400, detail=_dhan_setup_message())
    client = DhanClient(cfg.dhan)
    results: list[dict[str, Any]] = []
    from index_ai.learning import open_trades_for_mode

    for trade in open_trades_for_mode(cfg.risk.trading_mode):
        instrument_key = str(trade.get("instrument") or payload.get("instrument") or "NIFTY")
        if payload.get("instrument") and instrument_key != payload.get("instrument"):
            continue
        inst = get_instrument(instrument_key)
        quote = client.index_ltp(inst)
        price = float(quote.get("last_price") or quote.get("ltp") or trade["signal"]["price"])
        fresh_st = fetch_supertrend_snapshot(client, instrument_key)
        evaluation = evaluate_open_trade(trade, price, cfg.risk, fresh_supertrend=fresh_st)
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
    trade_id = str(payload.get("trade_id") or "").strip()
    if not trade_id:
        raise HTTPException(status_code=400, detail="trade_id is required")
    pnl_override = payload.get("pnl")
    cfg = settings()
    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    trade = _row_to_trade(row)
    if trade.get("pnl") is not None and pnl_override is None:
        raise HTTPException(status_code=400, detail="Trade already closed")

    if trade.get("pnl") is None and pnl_override is None and cfg.dhan.ready:
        try:
            from index_ai.exit import close_open_trade

            client = DhanClient(cfg.dhan)
            result = close_open_trade(
                trade,
                client=client,
                app_settings=cfg,
                reason=str(payload.get("note") or "Manual close from dashboard"),
            )
            return {"close": result, "learned": result.get("learned")}
        except Exception:
            pass

    learned = record_trade_outcome(
        trade_id=trade_id,
        pnl=float(pnl_override or 0),
        note=payload.get("note"),
    )
    return {"learned": learned}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse("/favicon.svg")


@app.get("/v2", include_in_schema=False)
@app.get("/v2/", include_in_schema=False)
async def dashboard_v2_redirect() -> RedirectResponse:
    """Legacy URL — React dashboard is now at /."""
    return RedirectResponse("/")


if (DASHBOARD_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
else:

    @app.get("/", include_in_schema=False)
    async def dashboard_missing() -> Response:
        html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Index Options AI</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;color:#e2e8f0;background:#0f172a}
a{color:#38bdf8}code{background:#1e293b;padding:.2rem .4rem;border-radius:.25rem}</style></head>
<body><h1>Dashboard not built</h1>
<p>Run <code>Start Index Options AI.cmd</code> and choose <strong>Start</strong>, or:</p>
<pre>cd dashboard\nnpm install\nnpm run build</pre>
<p>Then open <a href="/">this page</a> again.</p></body></html>"""
        return Response(content=html, media_type="text/html")


def configure_server_logging() -> Path:
    """Console + memory/server.log (Windows-friendly, unbuffered)."""
    import logging
    import sys

    class IstLogFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):  # noqa: N802
            dt = datetime.fromtimestamp(record.created, tz=ZoneInfo("Asia/Kolkata"))
            if datefmt:
                return dt.strftime(datefmt)
            return dt.strftime("%H:%M:%S IST")

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    log_path = MEMORY_DIR / "server.log"
    fmt = IstLogFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S IST",
    )
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    return log_path


def run() -> None:
    log_path = configure_server_logging()
    import logging

    logging.getLogger(__name__).info(
        "Index Options AI — dashboard http://127.0.0.1:8000/ log=%s", log_path
    )
    uvicorn.run(
        "index_ai.server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        use_colors=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()
