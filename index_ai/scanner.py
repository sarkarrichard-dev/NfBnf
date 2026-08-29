from __future__ import annotations

import asyncio
import logging
import time
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from index_ai.config import AppSettings, settings
from index_ai.dhan import DhanClient
from index_ai.dhan_errors import DhanAuthError, DhanRateLimitError, explain_dhan_http_error
from index_ai.executor import execute_plan
from index_ai.exit import close_open_trade
from index_ai.instruments import configured_index_keys, get_instrument, unconfigured_index_keys
from index_ai.learning import open_trades_for_mode, update_trade_trail_meta
from index_ai.market_clock import (
    format_ist_display,
    is_market_open,
    is_pre_open_analysis_window,
    is_square_off_window,
    is_trading_entries_allowed,
    market_status,
    now_ist,
    now_ist_iso,
    today_ist_date,
)
from index_ai.strategies.strategy_router import trade_lane
from index_ai.chart_live import fetch_supertrend_snapshot
from index_ai.planner import plan_instrument
from index_ai.risk import kill_switch_state
from index_ai.strategies.credit_spread import is_credit_option
from index_ai.mtm import enrich_open_trade_mtm
from index_ai.position_exits import (
    is_intraday_stale_open,
    strategy_exit_reason,
)
from index_ai.scan_health import ScanHealth, gather_limited, run_stage
from index_ai.trailing import evaluate_open_trade

_log_py = logging.getLogger(__name__)
_health = ScanHealth()

SCAN_INTERVAL_SECONDS = 90
COOLDOWN_MINUTES = 20
INDEX_SCAN_GAP_SECONDS = 5
# Indices scanned concurrently. Dhan rate-limits per second, so keep this small;
# 2-3 covers the configured universe in one round instead of N serial rounds.
INDEX_SCAN_CONCURRENCY = int(os.getenv("INDEX_SCAN_CONCURRENCY", "2"))
TRAIL_INDEX_GAP_SECONDS = 0.8
BOOT_AUTO_START_DELAY_SECONDS = 1.5


def auto_start_scanner_enabled() -> bool:
    return os.getenv("AUTO_START_SCANNER", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


@dataclass
class ScannerState:
    running: bool = False
    started_at: str | None = None
    last_cycle_at: str | None = None
    last_error: str | None = None
    last_execute_block: dict[str, Any] | None = None
    auth_blocked: bool = False
    cycles: int = 0
    executions: int = 0
    pre_open_brief_date: str | None = None
    pre_open_brief: dict[str, Any] | None = None
    last_reconcile: dict[str, Any] | None = None
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=80))


_state = ScannerState()
_task: asyncio.Task[None] | None = None


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, DhanAuthError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return explain_dhan_http_error(exc.response, "Dhan")
    return str(exc)


def _log(event: str, **fields: Any) -> None:
    entry = {"at": now_ist_iso(), "at_ist": market_status()["now_ist"], "event": event, **fields}
    _state.events.appendleft(entry)
    if fields:
        _log_py.info("%s | %s", event, " · ".join(f"{k}={v}" for k, v in fields.items()))
    else:
        _log_py.info("%s", event)


def scanner_status() -> dict[str, Any]:
    cfg = settings()
    ks = kill_switch_state(cfg.risk)
    mkt = market_status()
    return {
        "running": _state.running,
        "started_at": _state.started_at,
        "started_at_ist": format_ist_display(_state.started_at),
        "last_cycle_at": _state.last_cycle_at,
        "last_cycle_at_ist": format_ist_display(_state.last_cycle_at),
        "last_error": _state.last_error,
        "last_execute_block": _state.last_execute_block,
        "auth_blocked": _state.auth_blocked,
        "cycles": _state.cycles,
        "executions": _state.executions,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "indices": list(configured_index_keys()),
        "indices_skipped": list(unconfigured_index_keys()),
        "kill_switch": ks,
        "position_mode": cfg.risk.trading_mode,
        "open_trades": len(open_trades_for_mode(cfg.risk.trading_mode)),
        "open_trades_paper": len(open_trades_for_mode("PAPER")),
        "open_trades_live": len(open_trades_for_mode("LIVE")),
        "pre_open_brief": _state.pre_open_brief,
        "pre_open_brief_date": _state.pre_open_brief_date,
        "market": mkt,
        "health": _health.as_dict(),
        "index_scan_concurrency": INDEX_SCAN_CONCURRENCY,
        "last_reconcile": _state.last_reconcile,
        "events": list(_state.events),
    }


def _has_open_trade(instrument_key: str, *, mode: str, lane: str | None = None) -> bool:
    for t in open_trades_for_mode(mode):
        if str(t.get("instrument") or "") != instrument_key:
            continue
        if lane is None:
            return True
        if trade_lane(str(t.get("action") or "")) == lane:
            return True
    return False


def _recent_same_action(instrument_key: str, action: str, *, mode: str = "PAPER") -> bool:
    """Block repeat entries for the same index + strategy within the cooldown window."""
    from index_ai.learning import connect, is_live_trade
    from index_ai.market_clock import parse_ist_datetime

    normalized = str(mode or "PAPER").upper()
    cutoff = now_ist() - timedelta(minutes=COOLDOWN_MINUTES)
    with connect() as db:
        rows = db.execute(
            """
            SELECT created_at, mode, status FROM trades
            WHERE instrument = ? AND action = ?
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (instrument_key, action),
        ).fetchall()
    for row in rows:
        live_row = is_live_trade({"mode": row["mode"], "status": row["status"]})
        if normalized == "LIVE" and not live_row:
            continue
        if normalized != "LIVE" and live_row:
            continue
        created = parse_ist_datetime(str(row["created_at"] or ""))
        if created is not None and created >= cutoff:
            return True
    return False


def _note_auth_failure(exc: BaseException) -> None:
    if isinstance(exc, DhanAuthError):
        _state.auth_blocked = True
        _state.last_error = str(exc)


async def _close_trade(
    trade: dict[str, Any],
    client: DhanClient,
    cfg: AppSettings,
    *,
    reason: str,
    index_price: float | None = None,
) -> None:
    trade_id = str(trade["id"])
    try:
        result = close_open_trade(
            trade,
            client=client,
            app_settings=cfg,
            reason=reason,
            index_price=index_price,
        )
        _log(
            "auto_closed",
            trade_id=trade_id,
            instrument=trade.get("instrument"),
            pnl=result.get("pnl"),
            pnl_estimated=result.get("pnl_estimated"),
            reason=reason,
            index_price=index_price,
        )
    except Exception as exc:
        _note_auth_failure(exc)
        _log("close_error", trade_id=trade_id, error=_friendly_error(exc), reason=reason)


async def _fetch_index_prices(
    client: DhanClient,
    *,
    open_list: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """One LTP call per index with open trades (avoids duplicate 429s)."""
    trades = open_list if open_list is not None else []
    keys = {str(t.get("instrument") or "") for t in trades if t.get("instrument")}
    prices: dict[str, float] = {}
    for key in sorted(keys):
        try:
            quote = client.index_ltp(get_instrument(key))
            prices[key] = float(quote.get("last_price") or 0)
        except Exception as exc:
            _note_auth_failure(exc)
            _log("trail_check_error", instrument=key, error=_friendly_error(exc))
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)
    return prices


async def _close_stale_session_positions(client: DhanClient, cfg: AppSettings) -> None:
    """Flat positions carried from a prior IST day (missed EOD square-off)."""
    open_list = open_trades_for_mode(cfg.risk.trading_mode)
    stale = [t for t in open_list if is_intraday_stale_open(t)]
    if not stale:
        return
    prices = await _fetch_index_prices(client, open_list=stale)
    for trade in stale:
        key = str(trade.get("instrument") or "")
        await _close_trade(
            trade,
            client,
            cfg,
            reason="Prior session still open — flat at scanner (IST)",
            index_price=prices.get(key),
        )
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)


async def _apply_strategy_exits_for_index(
    client: DhanClient,
    cfg: AppSettings,
    instrument_key: str,
    action: str,
    regime: dict[str, Any],
    signal: dict[str, Any] | None = None,
) -> None:
    """Close open trades when CPR regime, EMA flip, or signal opposes the position."""
    active_mode = cfg.risk.trading_mode
    open_here = [
        t
        for t in open_trades_for_mode(active_mode)
        if str(t.get("instrument") or "") == instrument_key
    ]
    if not open_here:
        return
    prices = await _fetch_index_prices(client, open_list=open_here)
    for trade in open_here:
        reason = strategy_exit_reason(trade, action, regime, signal=signal)
        if not reason:
            continue
        await _close_trade(
            trade,
            client,
            cfg,
            reason=reason,
            index_price=prices.get(instrument_key),
        )
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)


async def _run_futures_paper(client: DhanClient) -> None:
    """Directional index-futures paper strategy — separate from the options path."""
    try:
        from index_ai.strategies.futures.paper import enabled, scan_futures_paper

        if not enabled():
            return
        events = await asyncio.to_thread(scan_futures_paper, client)
        for e in events:
            if e.get("event") in {"entry", "exit"}:
                _log("futures_paper", **{k: v for k, v in e.items() if k != "trade" or True})
    except Exception as exc:  # never let this break the options scanner
        _note_auth_failure(exc)
        _log("futures_paper_error", error=_friendly_error(exc))


async def _run_options_cpr_paper(client: DhanClient) -> None:
    """CPR + EMA option-buying paper strategy — separate from the options-sell path."""
    try:
        from index_ai.strategies.options_cpr.paper import enabled, scan_options_cpr_paper

        if not enabled():
            return
        events = await asyncio.to_thread(scan_options_cpr_paper, client)
        for e in events:
            if e.get("event") in {"entry", "exit", "partial"}:
                _log("options_cpr_paper", **e)
    except Exception as exc:  # never let this break the options scanner
        _note_auth_failure(exc)
        _log("options_cpr_paper_error", error=_friendly_error(exc))


async def _run_reconcile(client: DhanClient, cfg: AppSettings) -> None:
    """Broker-vs-journal drift check. Read-only unless RECONCILE_AUTO_REPAIR."""
    if cfg.risk.trading_mode != "LIVE":
        return
    from index_ai.reconcile import reconcile

    result = await asyncio.to_thread(reconcile, client, mode="LIVE")
    _state.last_reconcile = result
    if result.get("issues"):
        _log("reconcile_drift", issues=len(result["issues"]),
             kinds=sorted({i["kind"] for i in result["issues"]}),
             repaired=len(result.get("repaired") or []))


async def _check_trails(client: DhanClient, cfg: AppSettings) -> None:
    open_list = open_trades_for_mode(cfg.risk.trading_mode)
    prices = await _fetch_index_prices(client, open_list=open_list)
    keys = {str(t.get("instrument") or "") for t in open_list if t.get("instrument")}
    supertrends: dict[str, dict] = {}
    for key in sorted(keys):
        try:
            supertrends[key] = fetch_supertrend_snapshot(client, key)
        except Exception as exc:
            _note_auth_failure(exc)
            _log("supertrend_refresh_error", instrument=key, error=_friendly_error(exc))
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)

    for trade in open_list:
        key = str(trade.get("instrument") or "")
        price = prices.get(key)
        if price is None:
            continue
        try:
            work = dict(trade)
            if is_credit_option((trade.get("option") or {})):
                work = enrich_open_trade_mtm(work, client)
            elif work.get("pnl") is None:
                try:
                    work = enrich_open_trade_mtm(work, client)
                except Exception:
                    pass
            evaluation = evaluate_open_trade(
                work,
                price,
                cfg.risk,
                fresh_supertrend=supertrends.get(key),
            )
            update_trade_trail_meta(str(trade["id"]), evaluation["trail"])
            if evaluation.get("should_exit"):
                await _close_trade(
                    trade,
                    client,
                    cfg,
                    reason=str(evaluation.get("exit_reason") or "Trailing stop"),
                    index_price=price,
                )
                await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)
        except Exception as exc:
            _log("trail_check_error", instrument=key, error=_friendly_error(exc))


async def _square_off_open(client: DhanClient, cfg: AppSettings) -> None:
    open_list = open_trades_for_mode(cfg.risk.trading_mode)
    prices = await _fetch_index_prices(client, open_list=open_list)
    for trade in open_list:
        key = str(trade.get("instrument") or "")
        await _close_trade(
            trade,
            client,
            cfg,
            reason="End-of-session square-off (IST)",
            index_price=prices.get(key),
        )
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)


async def _run_pre_open_brief_if_due(client: DhanClient, cfg: AppSettings) -> None:
    """9:15–9:30 IST — refresh OI, spot volume, CPR, and EMA before first entry at 9:30."""
    if not is_pre_open_analysis_window():
        return

    from index_ai.pre_open_brief import build_pre_open_brief

    brief = await build_pre_open_brief(client, cfg)
    _state.pre_open_brief_date = today_ist_date()
    _state.pre_open_brief = brief
    _log(
        "pre_open_brief",
        summary=brief.get("summary"),
        confidence_bump=brief.get("confidence_bump"),
        notes=brief.get("notes"),
    )


async def _scan_index(
    client: DhanClient,
    cfg: AppSettings,
    instrument_key: str,
    *,
    allow_entries: bool = True,
) -> None:
    try:
        result = plan_instrument(client=client, app_settings=cfg, instrument_key=instrument_key)
    except Exception as exc:
        _note_auth_failure(exc)
        _log("scan_error", instrument=instrument_key, error=_friendly_error(exc))
        return

    if result.get("error"):
        _log("scan_skip", instrument=instrument_key, reason=result["error"])
        return

    signal_data = result.get("signal") or {}
    buy_data = result.get("buy_signal") or {}
    sell_data = result.get("sell_signal") or {}
    action = str(signal_data.get("action") or "NO_TRADE")
    plan_data = result.get("plan") or {}

    cpr = result.get("cpr_regime") or {}
    _log(
        "scan",
        instrument=instrument_key,
        action=action,
        buy_action=buy_data.get("action"),
        sell_action=sell_data.get("action"),
        confidence=signal_data.get("confidence"),
        cpr_regime=cpr.get("day_bias"),
        cpr_width_class=cpr.get("width_class"),
        plan_allowed=plan_data.get("allowed"),
        plan_reason=plan_data.get("reason"),
    )

    active_mode = cfg.risk.trading_mode
    await _apply_strategy_exits_for_index(client, cfg, instrument_key, action, cpr, signal_data)

    opportunities = list(result.get("opportunities") or [])
    if not opportunities and action != "NO_TRADE" and plan_data.get("allowed"):
        opportunities = [
            {
                "lane": trade_lane(action),
                "signal": signal_data,
                "option": result.get("option"),
                "plan": plan_data,
            }
        ]

    if not opportunities:
        if action == "NO_TRADE":
            _log(
                "no_trade",
                instrument=instrument_key,
                cpr_regime=cpr.get("day_bias"),
                buy_reason=str(buy_data.get("reason") or "")[:200],
                sell_reason=str(sell_data.get("reason") or "")[:200],
                strategy_mode=signal_data.get("strategy_mode"),
            )
        elif not plan_data.get("allowed"):
            _log(
                "plan_blocked",
                instrument=instrument_key,
                action=action,
                reason=str(plan_data.get("reason") or "")[:400],
            )
        return

    if not allow_entries:
        _log(
            "skip_entry_window",
            instrument=instrument_key,
            action=action,
            reason=market_status().get("message"),
        )
        return

    from index_ai.executor import ExecutionPlan
    from index_ai.market_clock import is_entry_session_timestamp, now_ist, trading_window_message

    if not is_entry_session_timestamp(now_ist()):
        _log(
            "skip_entry_window",
            instrument=instrument_key,
            action=action,
            reason=trading_window_message(now_ist()),
        )
        return

    for opp in opportunities:
        opp_signal = opp.get("signal") or {}
        opp_action = str(opp_signal.get("action") or "NO_TRADE")
        opp_plan = opp.get("plan") or {}
        lane = str(opp.get("lane") or trade_lane(opp_action))

        if opp_action == "NO_TRADE" or not opp_plan.get("allowed"):
            continue

        if _has_open_trade(instrument_key, mode=active_mode, lane=lane):
            _log(
                "skip_open_position",
                instrument=instrument_key,
                action=opp_action,
                lane=lane,
                mode=active_mode,
            )
            continue

        if _recent_same_action(instrument_key, opp_action, mode=active_mode):
            _log("skip_cooldown", instrument=instrument_key, action=opp_action, lane=lane)
            continue

        plan = ExecutionPlan(
            allowed=True,
            mode=str(opp_plan.get("mode") or cfg.risk.trading_mode),
            reason=str(opp_plan.get("reason") or ""),
            option=opp.get("option"),
            signal=opp_signal,
        )
        exec_result = execute_plan(plan, cfg, client)
        if exec_result.get("trade_id"):
            _state.executions += 1
            _state.last_error = None
            _state.last_execute_block = None
            _log(
                "executed",
                instrument=instrument_key,
                trade_id=exec_result.get("trade_id"),
                status=exec_result.get("status"),
                action=opp_action,
                lane=lane,
            )
        else:
            reason = str(
                exec_result.get("reason")
                or exec_result.get("status")
                or "Execution returned no trade_id"
            )
            _state.last_error = reason[:400]
            _state.last_execute_block = {
                "instrument": instrument_key,
                "action": opp_action,
                "lane": lane,
                "status": exec_result.get("status"),
                "reason": reason[:400],
                "at_ist": market_status()["now_ist"],
            }
            _log(
                "execute_blocked",
                instrument=instrument_key,
                action=opp_action,
                lane=lane,
                status=exec_result.get("status"),
                reason=reason,
            )


async def _run_loop() -> None:
    global _state
    _log("scanner_started", indices=list(configured_index_keys()), skipped=list(unconfigured_index_keys()))
    backoff = SCAN_INTERVAL_SECONDS

    while _state.running:
        cfg = settings()
        mkt = market_status()

        if not cfg.dhan.ready:
            _state.last_error = "Dhan not ready"
            _log("paused", reason=_state.last_error)
            await asyncio.sleep(backoff)
            continue

        if _state.auth_blocked:
            _log("paused", reason="auth_blocked", message=_state.last_error)
            await asyncio.sleep(backoff)
            continue

        if not is_market_open():
            _state.last_error = None
            _log("paused", reason="market_closed", message=mkt["message"])
            await asyncio.sleep(backoff)
            continue

        ks = kill_switch_state(cfg.risk)
        if ks["active"]:
            _state.last_error = "Kill switch active"
            _log("paused", reason="kill_switch", details=ks["reasons"])
            await asyncio.sleep(backoff)
            continue

        _state.last_error = None
        client = DhanClient(cfg.dhan)
        rate_limited = False
        entries_ok = is_trading_entries_allowed()
        cycle_started = time.monotonic()

        def _stage_failed(name: str, exc: BaseException) -> None:
            _note_auth_failure(exc)
            _log("stage_error", stage=name, error=_friendly_error(exc))

        async def _sync_live() -> None:
            if cfg.risk.trading_mode != "LIVE":
                return
            from index_ai.dhan_orders import sync_open_live_trades

            synced = await asyncio.to_thread(sync_open_live_trades, client)
            if synced:
                _log("live_orders_synced", updated=synced)

        try:
            # Stages are isolated: one failing step no longer aborts the cycle,
            # so a flaky lane can't stop trailing stops from being checked.
            for name, factory in (
                ("live_order_sync", _sync_live),
                ("pre_open_brief", lambda: _run_pre_open_brief_if_due(client, cfg)),
                ("stale_positions", lambda: _close_stale_session_positions(client, cfg)),
                ("trails", lambda: _check_trails(client, cfg)),
                ("futures_paper", lambda: _run_futures_paper(client)),
                ("options_cpr_paper", lambda: _run_options_cpr_paper(client)),
                ("reconcile", lambda: _run_reconcile(client, cfg)),
            ):
                if not _state.running:
                    break
                await run_stage(_health, name, factory, on_error=_stage_failed)

            if is_square_off_window():
                await run_stage(_health, "square_off",
                                lambda: _square_off_open(client, cfg), on_error=_stage_failed)
            else:
                keys = [k for k in configured_index_keys()]
                await run_stage(
                    _health,
                    "index_scans",
                    lambda: gather_limited(
                        [
                            (lambda k=k: _scan_index(client, cfg, k, allow_entries=entries_ok))
                            for k in keys
                        ],
                        limit=INDEX_SCAN_CONCURRENCY,
                    ),
                    timeout=SCAN_INTERVAL_SECONDS,
                    on_error=_stage_failed,
                )

            _state.cycles += 1
            _state.last_cycle_at = now_ist_iso()
            _health.last_cycle_ms = (time.monotonic() - cycle_started) * 1000
            backoff = SCAN_INTERVAL_SECONDS
        except DhanRateLimitError as exc:
            rate_limited = True
            _state.last_error = str(exc)
            _log("rate_limited", error=str(exc))
            backoff = min(180, SCAN_INTERVAL_SECONDS * 2)
        except Exception as exc:
            _note_auth_failure(exc)
            _state.last_error = _friendly_error(exc)
            _log("cycle_error", error=_state.last_error)

        await asyncio.sleep(backoff if rate_limited else SCAN_INTERVAL_SECONDS)


async def start_scanner() -> dict[str, Any]:
    global _task, _state
    if _state.running:
        return scanner_status()
    cfg = settings()
    if not cfg.dhan.ready:
        raise RuntimeError("Dhan is not ready. Complete login before starting auto trade.")
    _state.auth_blocked = False
    _state.running = True
    _state.started_at = now_ist_iso()
    _task = asyncio.create_task(_run_loop())
    return scanner_status()


async def bootstrap_scanner(*, respect_disable_flag: bool = True) -> dict[str, Any]:
    """Start the scanner when Dhan is ready (used on server boot and /api/auto/start)."""
    if respect_disable_flag and not auto_start_scanner_enabled():
        return {"started": False, "reason": "AUTO_START_SCANNER=false"}
    if _state.running:
        return {"started": True, "reason": "already_running", "status": scanner_status()}

    cfg = settings()
    if not cfg.dhan.ready:
        msg = "Dhan not ready — paste token in dashboard, then restart server or click Start."
        _state.last_error = msg
        _log("auto_start_skipped", reason=msg)
        return {"started": False, "reason": "dhan_not_ready", "message": msg}

    from index_ai.dhan_auth import check_dhan_health

    health = check_dhan_health(cfg.dhan)
    if not health.get("token_ok"):
        issues = "; ".join(health.get("issues") or ["Dhan token not accepted."])
        _state.last_error = issues
        _state.auth_blocked = True
        _log("auto_start_skipped", reason="token_invalid", message=issues)
        return {"started": False, "reason": "token_invalid", "message": issues}
    if not health.get("charts_ok"):
        issues = "; ".join(health.get("issues") or ["Intraday chart data unavailable."])
        _state.last_error = issues
        _log("auto_start_skipped", reason="charts_unavailable", message=issues)
        return {"started": False, "reason": "charts_unavailable", "message": issues}

    try:
        status = await start_scanner()
        _log("auto_start_ok", trigger="bootstrap")
        return {"started": True, "status": status}
    except RuntimeError as exc:
        _state.last_error = str(exc)
        _log("auto_start_failed", error=str(exc))
        return {"started": False, "reason": "runtime", "message": str(exc)}


async def schedule_boot_auto_start() -> None:
    """Deferred scanner start so startup token refresh can finish first."""
    await asyncio.sleep(BOOT_AUTO_START_DELAY_SECONDS)
    result = await bootstrap_scanner()
    if result.get("started"):
        _log("boot_auto_start", reason=result.get("reason") or "ok")
    else:
        _log("boot_auto_start_skipped", **{k: v for k, v in result.items() if k != "status"})


def queue_bootstrap_scanner() -> None:
    """Start scanner after Dhan login if it is not already running."""
    if _state.running:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(bootstrap_scanner())


def clear_auth_block() -> None:
    """Call after a fresh Dhan token is saved."""
    from index_ai.dhan_auth import clear_dhan_health_cache

    clear_dhan_health_cache()
    _state.auth_blocked = False
    _state.last_error = None


async def stop_scanner() -> dict[str, Any]:
    global _task, _state
    _state.running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _log("scanner_stopped")
    return scanner_status()
