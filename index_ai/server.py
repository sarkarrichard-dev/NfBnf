from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

from index_ai.admin_auth import require_admin_secret
from index_ai.analytics import build_analytics
from index_ai.reports import build_report, export_filename, report_to_csv
from index_ai.config import (
    ARM_LIVE_PHRASE,
    DASHBOARD_DIR,
    MEMORY_DIR,
    AppSettings,
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


def crypto_backtest_autotune_enabled() -> bool:
    """CRYPTO_BACKTEST_AUTOTUNE — off by default. crypto.ml.optimize.retune_all
    scores against downloaded historical candles, not the live journal;
    Richard rejected trusting that for tuning (2026-09-12: "i do not trust
    past data and testing... i want all testing on live data from the
    market"). Manual diagnostic only (POST /api/crypto/ml/optimize) bypasses
    this check and is unaffected."""
    from index_ai.env import env_bool

    return env_bool("CRYPTO_BACKTEST_AUTOTUNE", False)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from index_ai.single_instance import acquire_or_exit

    _quiet_http_loggers()  # covers `uvicorn index_ai.server:app`, which skips run()
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

    async def _crypto_paper_loop() -> None:
        """Crypto (Delta Exchange) lane — its own cadence, decoupled from the
        Dhan scanner because crypto trades 24/7. Paper by default; places real
        orders only when CRYPTO live is armed. Opt-in, never fatal."""
        try:
            from crypto.lanes import enabled as crypto_enabled, scan_crypto_paper
        except Exception:
            return
        _clog = logging.getLogger("crypto.lanes")
        while True:
            try:
                if crypto_enabled():
                    events = await asyncio.to_thread(scan_crypto_paper)
                    for e in events:
                        kind = e.get("event", "?")
                        if kind not in ("none", "hold", "wait"):
                            _clog.info(
                                "crypto_paper | %s",
                                " · ".join(f"{k}={v}" for k, v in e.items()),
                            )
            except Exception:
                _clog.warning("crypto paper loop error", exc_info=True)
            await asyncio.sleep(60)

    crypto_task = asyncio.create_task(_crypto_paper_loop())

    async def _commodities_paper_loop() -> None:
        """MCX commodity-futures lane — its own cadence, decoupled from the index
        scanner because MCX runs 09:00–23:30 IST, well past the equity close.
        Paper only, never wired to orders. Opt-in, never fatal."""
        try:
            from commodities.lanes import enabled as comm_enabled, scan_commodities_paper
        except Exception:
            return
        _mlog = logging.getLogger("commodities.lanes")
        while True:
            try:
                if comm_enabled():
                    events = await asyncio.to_thread(scan_commodities_paper)
                    for e in events:
                        if e.get("event") in {"entry", "exit", "error", "fetch_error"}:
                            _mlog.info(
                                "commodity_paper | %s",
                                " · ".join(f"{k}={v}" for k, v in e.items() if k != "trade"),
                            )
            except Exception:
                _mlog.warning("commodities paper loop error", exc_info=True)
            await asyncio.sleep(60)

    commodities_task = asyncio.create_task(_commodities_paper_loop())

    async def _crypto_nightly_loop() -> None:
        """Crypto ML retrain + day-review — once per UTC day, on its OWN task so
        it never blocks the 60s scan loop. A disk marker survives restarts so
        bouncing the server doesn't re-run it. Opt-in, never fatal.

        The backtest-based walk-forward auto-tune is separately gated — see
        crypto_backtest_autotune_enabled()."""
        try:
            from crypto.config import CRYPTO_MEMORY
            from crypto.lanes import enabled as crypto_enabled
        except Exception:
            return
        _clog = logging.getLogger("crypto.lanes")
        mark = CRYPTO_MEMORY / "crypto_maint_day.txt"
        while True:
            try:
                today = datetime.now(timezone.utc).date().isoformat()
                done = ""
                try:
                    done = mark.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                if crypto_enabled() and done != today:
                    try:
                        from crypto.ml.model import train as _crypto_train

                        r = await asyncio.to_thread(_crypto_train)
                        _clog.info("crypto ML retrain: %s", r.get("reason") or "trained")
                    except Exception:
                        _clog.warning("crypto ML retrain failed", exc_info=True)
                    if crypto_backtest_autotune_enabled():
                        try:
                            from crypto.ml.optimize import retune_all

                            await asyncio.to_thread(retune_all)
                            _clog.info("crypto strategy auto-tune done")
                        except Exception:
                            _clog.warning("crypto strategy auto-tune failed", exc_info=True)
                    else:
                        _clog.info(
                            "crypto strategy auto-tune skipped — backtest-based, off by "
                            "default (set CRYPTO_BACKTEST_AUTOTUNE=true to re-enable); "
                            "POST /api/crypto/ml/optimize runs it manually as a diagnostic"
                        )
                    try:
                        mark.parent.mkdir(parents=True, exist_ok=True)
                        mark.write_text(today, encoding="utf-8")
                    except OSError:
                        pass
                    _clog.info("crypto nightly maintenance done for %s", today)
            except Exception:
                _clog.warning("crypto nightly loop error", exc_info=True)
            await asyncio.sleep(1800)

    crypto_nightly_task = asyncio.create_task(_crypto_nightly_loop())

    async def _crypto_day_summary_loop() -> None:
        """Crypto end-of-day Telegram recap at 23:58 IST, once per IST day.
        Lists anything still open. Opt-in, never fatal."""
        try:
            from crypto.config import CRYPTO_MEMORY
            from crypto.day_review import send_day_summary
            from crypto.lanes import enabled as crypto_enabled
        except Exception:
            return
        ist = ZoneInfo("Asia/Kolkata")
        _clog = logging.getLogger("crypto.lanes")
        mark = CRYPTO_MEMORY / "crypto_day_summary.txt"
        while True:
            now = datetime.now(ist)
            target = now.replace(hour=23, minute=58, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep(max(30.0, (target - now).total_seconds()))
            try:
                day = datetime.now(ist).date().isoformat()
                done = mark.read_text(encoding="utf-8").strip() if mark.is_file() else ""
                if crypto_enabled() and done != day:
                    r = await asyncio.to_thread(send_day_summary)
                    _clog.info("crypto day summary %s: %s", day, r)
                    mark.parent.mkdir(parents=True, exist_ok=True)
                    mark.write_text(day, encoding="utf-8")
            except Exception:
                _clog.warning("crypto day summary loop error", exc_info=True)

    crypto_day_summary_task = asyncio.create_task(_crypto_day_summary_loop())

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

    async def _eod_catch_up() -> None:
        """If the server is (re)started after square-off and today's EOD summary
        never ran — because the scanner was idle — run it once from here. The
        eod_date marker in run_eod keeps it to once per day."""
        await asyncio.sleep(20)
        try:
            from index_ai import scanner
            from index_ai.daily_ops import eod_due, run_eod

            if not eod_due():
                return
            if not getattr(scanner._state, "running", False):
                logging.getLogger("index_ai.daily_ops").info(
                    "eod: scanner idle — running from boot catch-up"
                )
            await asyncio.to_thread(run_eod)
        except Exception:
            logging.getLogger("index_ai.daily_ops").warning(
                "eod boot catch-up failed", exc_info=True
            )

    eod_catch_up_task = asyncio.create_task(_eod_catch_up())
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
    crypto_task.cancel()
    commodities_task.cancel()
    crypto_nightly_task.cancel()
    crypto_day_summary_task.cancel()
    eod_catch_up_task.cancel()
    for task in (
        renew_task,
        boot_scanner_task,
        cache_task,
        tick_task,
        warm_task,
        crypto_task,
        commodities_task,
        crypto_nightly_task,
        crypto_day_summary_task,
        eod_catch_up_task,
    ):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await stop_scanner()


app = FastAPI(title="Index Options AI", version="0.2.0", lifespan=lifespan)

# The API has no auth and can arm live orders. It's bound to 127.0.0.1, but a
# web page in the operator's browser can still reach it by rebinding a hostname
# it controls to 127.0.0.1 (DNS-rebinding). A Host-header allowlist closes that
# vector: the browser sends the attacker's Host, which isn't in the list, so the
# request is rejected before it hits a handler. Necessary, not sufficient — real
# auth still needs adding before this leaves localhost (cloud security baseline).
# ALLOWED_HOSTS is env-driven so a reverse proxy / container health probe in the
# cloud shape can widen it without a code change. `*.ts.net` is in the default so
# `tailscale serve` (a private tailnet, HTTPS, proxied to loopback — never a
# public bind) works with no config; see "Share on Tailnet.cmd".
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        h.strip()
        for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver,*.ts.net").split(
            ","
        )
        if h.strip()
    ],
)

# Optional shared-password gate (HTTP Basic on every route incl. the dashboard).
# Off unless DASHBOARD_PASSWORD is set. Meant for the tailnet-sharing case: a
# tester clicking around should not be able to flip Paper→Live, edit lot sizes,
# or purge test data by accident. Any username; the password is the secret.
_DASHBOARD_PW = os.getenv("DASHBOARD_PASSWORD", "").strip()

if _DASHBOARD_PW:
    import base64  # noqa: E402
    import secrets  # noqa: E402

    from starlette.requests import Request as _Req  # noqa: E402
    from starlette.responses import PlainTextResponse as _PlainResp  # noqa: E402

    @app.middleware("http")
    async def _basic_auth(request: "_Req", call_next):  # type: ignore[no-untyped-def]
        hdr = request.headers.get("authorization", "")
        ok = False
        if hdr[:6].lower() == "basic ":  # RFC 7617 — scheme token is case-insensitive
            try:
                _, _, pw = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(pw.encode("utf-8"), _DASHBOARD_PW.encode("utf-8"))
            except Exception:
                ok = False
        if not ok:
            return _PlainResp(
                "Authentication required.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="QuantHawk"'},
            )
        return await call_next(request)


# Crypto section (Delta Exchange) — separate lane, its own /api/crypto surface.
try:
    from crypto.api import router as crypto_router

    app.include_router(crypto_router)
except Exception as _crypto_exc:  # never let the crypto module stop the index server
    logging.getLogger(__name__).warning("crypto router not mounted: %s", _crypto_exc)

# Investing section (NSE stock screener) — read-only research, no orders.
try:
    from investing.api import router as investing_router

    app.include_router(investing_router)
except Exception as _investing_exc:
    logging.getLogger(__name__).warning("investing router not mounted: %s", _investing_exc)

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
        # panels the dashboard should render — flipped from Setup → Feature toggles
        # (HIDE_* flags) so a shared team view can drop the owner's broker account
        # and the unfinished Builder without a code change.
        "ui": {
            f["flag"].removeprefix("HIDE_").lower(): not f["enabled"]
            for f in feature_flags()
            if f["flag"].startswith("HIDE_")
        },
    }


@app.get("/api/ticker", include_in_schema=False)
async def ticker() -> dict[str, Any]:
    from index_ai.ticker import live_ticker

    return await asyncio.to_thread(live_ticker, settings())


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
def export_report(  # sync: build_report does SQLite + Dhan I/O — Starlette threadpools it
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
async def trading_mode(
    payload: dict[str, Any] = Body(default_factory=dict),
    _admin: None = Depends(require_admin_secret),
) -> dict[str, Any]:
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


@app.get("/api/futures/journal", include_in_schema=False)
def futures_journal_api(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """Closed futures paper trades (trade-shaped), newest first — for the
    Reports / Trade-history 'Futures' source."""
    from index_ai.strategies.futures.paper import _recent_trades

    return {"trades": _recent_trades(limit)}


@app.post("/api/futures/positions/close", include_in_schema=False)
async def futures_close_position(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Manual "Close" button on the dashboard — force-exit one open futures
    position now, at the current mark."""
    from index_ai.strategies.futures.paper import close_position_manual

    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="key is required")
    client = DhanClient(settings().dhan)
    return await asyncio.to_thread(close_position_manual, key, client)


@app.post("/api/futures/positions/close-all", include_in_schema=False)
async def futures_close_all_positions() -> dict[str, Any]:
    """Manual "Close all" button — force-exit every open futures position now."""
    from index_ai.strategies.futures.paper import close_all_positions_manual

    client = DhanClient(settings().dhan)
    return await asyncio.to_thread(close_all_positions_manual, client)


@app.get("/api/commodities/status", include_in_schema=False)
def commodities_status_api() -> dict[str, Any]:  # sync
    from commodities.lanes import commodities_status

    return commodities_status()


@app.get("/api/commodities/journal", include_in_schema=False)
def commodities_journal_api(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """Closed MCX commodity paper trades (trade-shaped), newest first — for the
    Reports / Trade-history 'Commodities' source."""
    from commodities.lanes import _recent

    return {"trades": _recent(limit)[::-1]}


@app.post("/api/commodities/positions/close", include_in_schema=False)
async def commodities_close_position(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Manual "Close" button on the dashboard — force-exit one open commodity
    position now, at the current mark."""
    from commodities.lanes import close_position_manual

    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="key is required")
    return await asyncio.to_thread(close_position_manual, key)


@app.post("/api/commodities/positions/close-all", include_in_schema=False)
async def commodities_close_all_positions() -> dict[str, Any]:
    """Manual "Close all" button — force-exit every open commodity position now."""
    from commodities.lanes import close_all_positions_manual

    return await asyncio.to_thread(close_all_positions_manual)


@app.get("/api/futures/backtest", include_in_schema=False)
def futures_backtest_api() -> dict[str, Any]:
    """Static replay results for the Futures tab — the stock-futures backtest
    (`scripts/backtest_stock_futures`) and the index one, whichever have been run.
    Empty sub-objects when a backtest hasn't produced a file yet."""
    import json as _json
    from pathlib import Path

    def _load(p: str) -> dict[str, Any]:
        f = Path(p)
        try:
            return _json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        except (OSError, ValueError):
            return {}

    return {
        "stock": _load("research/stock_futures/summary.json"),
        "index": _load("research/futures/summary.json"),
        "generated_at_ist": format_ist_display(now_ist_iso()),
    }


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
async def arm_live(
    payload: dict[str, Any] = Body(default_factory=dict),
    _admin: None = Depends(require_admin_secret),
) -> dict[str, Any]:
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


@app.get("/api/day-review", include_in_schema=False)
async def day_review_api(refresh: bool = Query(False)) -> dict[str, Any]:
    """Today's trades (with why-in / why-out), a summary, and an advisory AI review.

    refresh=true re-reads the journal and re-asks the LLM (costs a token call);
    otherwise returns the cached copy, regenerated after each square-off.
    """
    from index_ai.day_review import build_day_review

    return await asyncio.to_thread(build_day_review, refresh=refresh)


@app.get("/api/strategy-performance", include_in_schema=False)
async def strategy_performance_api() -> dict[str, Any]:
    """Per-(strategy, instrument) scorecard from the live journals — trades,
    win rate, gross, the Dhan/Delta charges paid, and net. Read-only."""
    from index_ai.strategy_performance import strategy_scorecard

    return await asyncio.to_thread(strategy_scorecard)


@app.get("/api/crypto/live-readiness", include_in_schema=False)
async def crypto_live_readiness_api() -> dict[str, Any]:
    """Per-strategy go-live bar: enough trades, enough days, net positive —
    agreed with Richard 2026-09-17 rather than picking a live date up front."""
    from index_ai.strategy_performance import crypto_live_readiness

    return {"strategies": await asyncio.to_thread(crypto_live_readiness)}


@app.get("/api/strategy-learning", include_in_schema=False)
async def strategy_learning_api() -> dict[str, Any]:
    """The confidence ladder per (strategy, instrument): how much data each has,
    what state it's in (watching / observing / ready / frozen), and flagged
    entry patterns. Nothing here changes a strategy. Read-only."""
    from index_ai.strategy_learning import learning_report

    return await asyncio.to_thread(learning_report)


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
    return await asyncio.to_thread(execute_plan, plan, cfg, client)


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


def _live_index_price(client: DhanClient | None, instrument_key: str) -> float | None:
    """Best-effort spot LTP for the manual-close PnL fallback — never raises,
    a quote failure just means close_open_trade falls back to its own
    estimate instead of blocking the close."""
    if client is None:
        return None
    try:
        inst = get_instrument(instrument_key)
        quote = client.index_ltp(inst)
        price = quote.get("last_price") or quote.get("ltp")
        return float(price) if price is not None else None
    except Exception:
        return None


def _close_trade_by_id_sync(trade_id: str, cfg: AppSettings) -> dict[str, Any]:
    """The full manual-close for one India index-options trade — DB lookup,
    live-price fetch, and the close itself — run entirely on a worker thread
    (see the async wrapper below). Doing the DB read and the Dhan LTP call
    directly in an async handler body would block the event loop for every
    concurrent dashboard poll, the exact class of bug CLAUDE.md flags as
    having bitten this codebase before."""
    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not row:
        return {"status": "NOT_FOUND", "trade_id": trade_id}
    trade = _row_to_trade(row)
    if trade.get("pnl") is not None:
        return {"status": "ALREADY_CLOSED", "trade_id": trade_id}

    client = DhanClient(cfg.dhan) if cfg.dhan.ready else None
    index_price = _live_index_price(client, str(trade.get("instrument") or "NIFTY"))
    return close_open_trade(
        trade,
        client=client,
        app_settings=cfg,
        reason="manual close",
        index_price=index_price,
    )


def _close_all_trades_sync(cfg: AppSettings) -> dict[str, Any]:
    """Close-all — same worker-thread reasoning as _close_trade_by_id_sync,
    for the whole loop (open_trades() is a DB read; each trade needs its own
    LTP fetch and close)."""
    open_now = open_trades()
    results = {str(t["id"]): _close_trade_by_id_sync(str(t["id"]), cfg) for t in open_now}
    failed = {
        k: v for k, v in results.items() if v.get("status") not in {"CLOSED", "ALREADY_CLOSED"}
    }
    return {"ok": not failed, "attempted": len(open_now), "results": results}


@app.post("/api/trades/positions/close", include_in_schema=False)
async def trades_close_position(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Manual "Close" button on the dashboard — force-exit one open India
    index-options trade (NIFTY/BANKNIFTY/SENSEX) now, at the current price.
    Reuses the exact same close_open_trade() the automatic trail/EOD paths
    use, so a manual close can never compute P&L differently than an
    automatic one — including sending a real opposite-side order when the
    trade is LIVE and live trading is armed."""
    trade_id = str(payload.get("trade_id") or "").strip()
    if not trade_id:
        raise HTTPException(status_code=422, detail="trade_id is required")
    result = await asyncio.to_thread(_close_trade_by_id_sync, trade_id, settings())
    if result.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return result


@app.post("/api/trades/positions/close-all", include_in_schema=False)
async def trades_close_all_positions() -> dict[str, Any]:
    """Manual "Close all" button — force-exit every open India index-options
    trade now (any instrument, paper or live), each at its own current price.
    A trade that fails to close doesn't stop the rest."""
    return await asyncio.to_thread(_close_all_trades_sync, settings())


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
    _quiet_http_loggers()
    return log_path


def _quiet_http_loggers() -> None:
    """httpx logs every outbound request URL at INFO — one line per Dhan/Delta
    call, which floods server.log and could carry a query-string credential into
    it (and its backup). Keep the HTTP client loggers at WARNING. Called from
    both start paths — run() and lifespan()."""
    for name in ("httpx", "httpcore", "hpack", "h11"):
        logging.getLogger(name).setLevel(logging.WARNING)


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
