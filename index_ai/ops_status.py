"""Live operations / diagnostics for the dashboard."""

from __future__ import annotations

from typing import Any

from index_ai.config import MEMORY_DIR, settings
from index_ai.market_clock import (
    format_ist_display,
    is_entry_session_timestamp,
    is_market_open,
    is_trading_entries_allowed,
    market_status,
    now_ist,
    now_ist_iso,
)
from index_ai.scanner import scanner_status


def _read_log_tail(max_lines: int = 80) -> list[str]:
    log_path = MEMORY_DIR / "server.log"
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max_lines:]
    except OSError:
        return []


def build_ops_status() -> dict[str, Any]:
    from index_ai.server import _trading_gates

    cfg = settings()
    mkt = market_status()
    auto = scanner_status()
    tg = _trading_gates(cfg)
    mode = str(cfg.risk.trading_mode or "PAPER").upper()

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    ok: list[dict[str, str]] = []

    if not cfg.dhan.ready:
        blockers.append(
            {
                "code": "dhan",
                "title": "Dhan not ready",
                "detail": "Token missing or expired — use Dhan Login panel.",
            }
        )
    else:
        ok.append({"code": "dhan", "title": "Dhan", "detail": "API token OK"})

    if not auto.get("running"):
        blockers.append(
            {
                "code": "scanner",
                "title": "Scanner stopped",
                "detail": "Click Start in Auto trader (or Start algo from .cmd).",
            }
        )
    else:
        ok.append(
            {
                "code": "scanner",
                "title": "Scanner",
                "detail": f"Running · {auto.get('cycles', 0)} cycles · {auto.get('executions', 0)} entries",
            }
        )

    if auto.get("auth_blocked"):
        blockers.append(
            {
                "code": "auth",
                "title": "Dhan auth blocked",
                "detail": str(auto.get("last_error") or "Refresh token in Dhan Login."),
            }
        )

    if not is_market_open():
        blockers.append(
            {
                "code": "market_closed",
                "title": "Market closed",
                "detail": str(mkt.get("message") or "Outside NSE session."),
            }
        )
    elif not is_trading_entries_allowed():
        warnings.append(
            {
                "code": "entry_window",
                "title": "Entry window closed",
                "detail": str(mkt.get("message") or "Square-off / pre-entry only."),
            }
        )
    else:
        ok.append(
            {
                "code": "market",
                "title": "Market",
                "detail": str(mkt.get("message") or "Session open — entries allowed."),
            }
        )

    if mode == "LIVE":
        if not tg.get("can_send_live_orders"):
            for item in tg.get("reasons") or []:
                title = str(item.get("title") or "")
                if title in {"Kill switch ACTIVE", "Live flag off", "Dhan not ready"}:
                    blockers.append(
                        {
                            "code": title.lower().replace(" ", "_"),
                            "title": title,
                            "detail": str(item.get("detail") or ""),
                        }
                    )
        else:
            ok.append(
                {
                    "code": "live_orders",
                    "title": "Live orders",
                    "detail": "Broker orders enabled (TRADING_MODE=LIVE).",
                }
            )
    else:
        ok.append(
            {
                "code": "paper",
                "title": "Paper mode",
                "detail": "Algo records trades in local journal only — not sent to Dhan.",
            }
        )

    if auto.get("last_error") and not auto.get("auth_blocked"):
        warnings.append(
            {
                "code": "scanner_error",
                "title": "Last scanner message",
                "detail": str(auto["last_error"])[:400],
            }
        )

    from index_ai.strategies.strategy_params import get_strategy_params

    sp = get_strategy_params()
    if sp.auto_credit_sideways_only and sp.auto_trend_buy_first:
        warnings.append(
            {
                "code": "strategy_routing",
                "title": "Trending days: buy-only",
                "detail": (
                    "AUTO_CREDIT_SIDEWAYS_ONLY=true blocks credit spreads on trending CPR. "
                    "Set false to allow bull put / bear call on trend days."
                ),
            }
        )
    elif sp.auto_buy_trending_only and not sp.auto_credit_sideways_only:
        ok.append(
            {
                "code": "strategy_routing",
                "title": "Dual-lane AUTO",
                "detail": (
                    "Buy: candlestick patterns at 1m S/R (mid-day trend OK). "
                    f"Sell: CPR pivot/BC/TC + EMA {sp.ema_fast_period}/{sp.ema_slow_period} + volume."
                ),
            }
        )

    last_block = auto.get("last_execute_block")
    if last_block:
        warnings.append(
            {
                "code": "last_execute_block",
                "title": "Last blocked entry",
                "detail": (
                    f"{last_block.get('instrument')} {last_block.get('action')}: "
                    f"{last_block.get('reason')}"
                )[:400],
            }
        )

    can_enter = (
        cfg.dhan.ready
        and bool(auto.get("running"))
        and not auto.get("auth_blocked")
        and is_entry_session_timestamp(now_ist())
        and (mode == "PAPER" or bool(tg.get("can_send_live_orders")))
    )

    return {
        "updated_at_ist": format_ist_display(now_ist_iso()),
        "trading_mode": mode,
        "can_enter_trades": can_enter,
        "can_send_live_orders": bool(tg.get("can_send_live_orders")),
        "blockers": blockers,
        "warnings": warnings,
        "ok": ok,
        "market": mkt,
        "scanner": {
            "running": auto.get("running"),
            "cycles": auto.get("cycles"),
            "executions": auto.get("executions"),
            "last_cycle_at_ist": auto.get("last_cycle_at_ist"),
            "last_error": auto.get("last_error"),
            "last_execute_block": last_block,
            "position_mode": auto.get("position_mode"),
            "open_trades_paper": auto.get("open_trades_paper"),
            "open_trades_live": auto.get("open_trades_live"),
        },
        "events": (auto.get("events") or [])[:40],
        "log_tail": _read_log_tail(),
        "log_file": str(MEMORY_DIR / "server.log"),
        "trading_gates": tg,
    }


def preview_scan(instrument_key: str = "NIFTY") -> dict[str, Any]:
    """One-shot plan preview (no order) — for debugging why entries are blocked."""
    from index_ai.dhan import DhanClient
    from index_ai.planner import plan_instrument

    cfg = settings()
    if not cfg.dhan.ready:
        return {"error": "Dhan not ready", "instrument": instrument_key}
    try:
        result = plan_instrument(
            client=DhanClient(cfg.dhan),
            app_settings=cfg,
            instrument_key=instrument_key.strip().upper(),
        )
    except Exception as exc:
        return {"error": str(exc), "instrument": instrument_key}

    signal = result.get("signal") or {}
    plan = result.get("plan") or {}
    buy = result.get("buy_signal") or {}
    sell = result.get("sell_signal") or {}
    return {
        "instrument": instrument_key,
        "action": signal.get("action"),
        "buy_action": buy.get("action"),
        "sell_action": sell.get("action"),
        "confidence": signal.get("confidence"),
        "reason": signal.get("reason"),
        "buy_reason": buy.get("reason"),
        "sell_reason": sell.get("reason"),
        "plan_allowed": plan.get("allowed"),
        "plan_reason": plan.get("reason"),
        "plan_mode": plan.get("mode"),
        "cpr_regime": result.get("cpr_regime"),
        "error": result.get("error"),
        "market": market_status(),
    }
