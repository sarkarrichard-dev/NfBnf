from __future__ import annotations

from typing import Any

from index_ai.credit_spread import compute_credit_mtm, is_credit_option
from index_ai.dhan import DhanClient
from index_ai.exit import estimate_pnl_rupees, option_ltp_with_retry
from index_ai.market_clock import format_ist_display, now_ist_iso

MTM_HISTORY_LIMIT = 48


def compute_mtm_pnl(trade: dict[str, Any], option_ltp: float) -> float:
    option = trade.get("option") or {}
    entry_ltp = float(option.get("ltp") or 0)
    qty = int(option.get("quantity") or 1)
    tx = str(option.get("transaction_type") or "BUY").upper()
    return estimate_pnl_rupees(
        entry_ltp=entry_ltp,
        exit_ltp=float(option_ltp),
        quantity=qty,
        transaction_type=tx,
    )


def _append_mtm_history(option: dict[str, Any], snapshot: dict[str, Any]) -> None:
    history = list(option.get("mtm_history") or [])
    history.append(snapshot)
    option["mtm_history"] = history[-MTM_HISTORY_LIMIT:]


def persist_trade_option(trade_id: str, option: dict[str, Any]) -> None:
    import json

    from index_ai.learning import connect

    with connect() as db:
        db.execute(
            "UPDATE trades SET option_json = ? WHERE id = ?",
            (json.dumps(option, default=str), trade_id),
        )


def enrich_open_trade_mtm(trade: dict[str, Any], client: DhanClient) -> dict[str, Any]:
    """Fetch live option LTP and attach MTM + short history on the trade dict."""
    if trade.get("pnl") is not None:
        return trade

    from index_ai.learning import sync_option_lot_size

    trade = sync_option_lot_size(trade, persist=True)
    option = dict(trade.get("option") or {})
    try:
        if is_credit_option(option):
            mtm, close_debit, leg_ltps = compute_credit_mtm(option, client)
            now = now_ist_iso()
            option["last_option_ltp"] = close_debit
            option["last_close_debit"] = close_debit
            option["leg_ltps"] = leg_ltps
            option["mtm_pnl"] = mtm
            option["mtm_updated_at"] = now
            _append_mtm_history(
                option,
                {
                    "at": now,
                    "at_ist": format_ist_display(now),
                    "option_ltp": close_debit,
                    "close_debit": close_debit,
                    "mtm_pnl": mtm,
                },
            )
        else:
            ltp = option_ltp_with_retry(client, option, attempts=2)
            mtm = compute_mtm_pnl(trade, ltp)
            now = now_ist_iso()
            option["last_option_ltp"] = ltp
            option["mtm_pnl"] = mtm
            option["mtm_updated_at"] = now
            _append_mtm_history(
                option,
                {
                    "at": now,
                    "at_ist": format_ist_display(now),
                    "option_ltp": ltp,
                    "mtm_pnl": mtm,
                },
            )
        trade["option"] = option
        persist_trade_option(str(trade["id"]), option)
    except Exception as exc:
        option["mtm_error"] = str(exc)[:200]
        trade["option"] = option
    return trade


def enrich_open_trades_mtm(trades: list[dict[str, Any]], client: DhanClient | None) -> list[dict[str, Any]]:
    if client is None:
        return trades
    out: list[dict[str, Any]] = []
    for trade in trades:
        if trade.get("pnl") is not None:
            out.append(trade)
            continue
        out.append(enrich_open_trade_mtm(trade, client))
    return out
