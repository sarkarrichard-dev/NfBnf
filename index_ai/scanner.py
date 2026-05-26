from __future__ import annotations

import asyncio
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
from index_ai.learning import open_trades, update_trade_trail_meta
from index_ai.market_clock import (
    is_market_open,
    is_square_off_window,
    market_status,
    now_ist,
    now_ist_iso,
)
from index_ai.planner import plan_instrument
from index_ai.risk import kill_switch_state
from index_ai.trailing import evaluate_open_trade

SCAN_INTERVAL_SECONDS = 90
COOLDOWN_MINUTES = 20
INDEX_SCAN_GAP_SECONDS = 5
TRAIL_INDEX_GAP_SECONDS = 0.8


@dataclass
class ScannerState:
    running: bool = False
    started_at: str | None = None
    last_cycle_at: str | None = None
    last_error: str | None = None
    auth_blocked: bool = False
    cycles: int = 0
    executions: int = 0
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


def scanner_status() -> dict[str, Any]:
    cfg = settings()
    ks = kill_switch_state(cfg.risk)
    mkt = market_status()
    return {
        "running": _state.running,
        "started_at": _state.started_at,
        "started_at_ist": _state.started_at,
        "last_cycle_at": _state.last_cycle_at,
        "last_error": _state.last_error,
        "auth_blocked": _state.auth_blocked,
        "cycles": _state.cycles,
        "executions": _state.executions,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "indices": list(configured_index_keys()),
        "indices_skipped": list(unconfigured_index_keys()),
        "kill_switch": ks,
        "open_trades": len(open_trades()),
        "market": mkt,
        "events": list(_state.events),
    }


def _has_open_trade(instrument_key: str) -> bool:
    return any(str(t.get("instrument") or "") == instrument_key for t in open_trades())


def _recent_same_action(instrument_key: str, action: str) -> bool:
    cutoff = (now_ist() - timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
    from index_ai.learning import connect

    with connect() as db:
        row = db.execute(
            """
            SELECT id FROM trades
            WHERE instrument = ? AND action = ? AND created_at >= ?
            LIMIT 1
            """,
            (instrument_key, action, cutoff),
        ).fetchone()
    return row is not None


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


async def _fetch_index_prices(client: DhanClient) -> dict[str, float]:
    """One LTP call per index with open trades (avoids duplicate 429s)."""
    keys = {str(t.get("instrument") or "") for t in open_trades() if t.get("instrument")}
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


async def _check_trails(client: DhanClient, cfg: AppSettings) -> None:
    prices = await _fetch_index_prices(client)
    for trade in open_trades():
        key = str(trade.get("instrument") or "")
        price = prices.get(key)
        if price is None:
            continue
        try:
            evaluation = evaluate_open_trade(trade, price, cfg.risk)
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
    prices = await _fetch_index_prices(client)
    for trade in open_trades():
        key = str(trade.get("instrument") or "")
        await _close_trade(
            trade,
            client,
            cfg,
            reason="End-of-session square-off (IST)",
            index_price=prices.get(key),
        )
        await asyncio.sleep(TRAIL_INDEX_GAP_SECONDS)


async def _scan_index(client: DhanClient, cfg: AppSettings, instrument_key: str) -> None:
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
    action = str(signal_data.get("action") or "NO_TRADE")
    plan_data = result.get("plan") or {}

    _log(
        "scan",
        instrument=instrument_key,
        action=action,
        confidence=signal_data.get("confidence"),
        plan_allowed=plan_data.get("allowed"),
        plan_reason=plan_data.get("reason"),
    )

    if action == "NO_TRADE" or not plan_data.get("allowed"):
        return

    if _has_open_trade(instrument_key):
        _log("skip_open_position", instrument=instrument_key, action=action)
        return

    if _recent_same_action(instrument_key, action):
        _log("skip_cooldown", instrument=instrument_key, action=action)
        return

    from index_ai.executor import ExecutionPlan

    plan = ExecutionPlan(
        allowed=True,
        mode=str(plan_data.get("mode") or cfg.risk.trading_mode),
        reason=str(plan_data.get("reason") or ""),
        option=result.get("option"),
        signal=signal_data,
    )
    exec_result = execute_plan(plan, cfg, client)
    if exec_result.get("trade_id"):
        _state.executions += 1
        _log(
            "executed",
            instrument=instrument_key,
            trade_id=exec_result.get("trade_id"),
            status=exec_result.get("status"),
            action=action,
        )
    else:
        _log("execute_blocked", instrument=instrument_key, detail=exec_result)


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
        try:
            await _check_trails(client, cfg)

            if is_square_off_window():
                await _square_off_open(client, cfg)
            else:
                for key in configured_index_keys():
                    if not _state.running:
                        break
                    await _scan_index(client, cfg, key)
                    await asyncio.sleep(INDEX_SCAN_GAP_SECONDS)

            _state.cycles += 1
            _state.last_cycle_at = now_ist_iso()
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


def clear_auth_block() -> None:
    """Call after a fresh Dhan token is saved."""
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
