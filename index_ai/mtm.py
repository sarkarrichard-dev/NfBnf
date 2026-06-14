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


def enrich_open_trade_mtm(
    trade: dict[str, Any],
    client: DhanClient,
    *,
    persist: bool = True,
    ltp_cache: dict[tuple[str, int], float] | None = None,
) -> dict[str, Any]:
    """Fetch live option LTP and attach MTM + short history on the trade dict."""
    if trade.get("pnl") is not None:
        return trade

    from index_ai.learning import sync_option_lot_size

    trade = sync_option_lot_size(trade, persist=False)
    option = dict(trade.get("option") or {})
    try:
        if is_credit_option(option):
            mtm, close_debit, leg_ltps = compute_credit_mtm(
                option,
                client,
                instrument_key=str(trade.get("instrument") or option.get("instrument") or ""),
                ltp_cache=ltp_cache,
            )
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
            ltp = _ltp_from_cache_or_fetch(client, option, ltp_cache)
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
        if persist:
            persist_trade_option(str(trade["id"]), option)
    except Exception as exc:
        option["mtm_error"] = str(exc)[:200]
        trade["option"] = option
    return trade


def _ltp_from_cache_or_fetch(
    client: DhanClient,
    option: dict[str, Any],
    ltp_cache: dict[tuple[str, int], float] | None,
) -> float:
    segment = str(option.get("segment") or "")
    sid = int(option["security_id"])
    if ltp_cache is not None:
        cached = ltp_cache.get((segment, sid))
        if cached is not None:
            return cached
    return option_ltp_with_retry(client, option, attempts=1)


def _build_ltp_cache(client: DhanClient, trades: list[dict[str, Any]]) -> dict[tuple[str, int], float]:
    """One marketfeed LTP request per segment for all open legs (fast poll path)."""
    from index_ai.credit_spread import _parse_ltp_bucket

    by_segment: dict[str, set[int]] = {}
    for trade in trades:
        if trade.get("pnl") is not None:
            continue
        option = trade.get("option") or {}
        for leg in option.get("legs") or [option]:
            if not isinstance(leg, dict) or leg.get("security_id") is None:
                continue
            seg = str(leg.get("segment") or option.get("segment") or "NSE_FNO")
            by_segment.setdefault(seg, set()).add(int(leg["security_id"]))
        if option.get("security_id") is not None and not option.get("legs"):
            seg = str(option.get("segment") or "NSE_FNO")
            by_segment.setdefault(seg, set()).add(int(option["security_id"]))

    cache: dict[tuple[str, int], float] = {}
    for segment, ids in by_segment.items():
        if not ids:
            continue
        try:
            raw = client.ltp(segment, sorted(ids))
            for sid in ids:
                px = _parse_ltp_bucket(raw, segment, sid)
                if px is not None:
                    cache[(segment, sid)] = px
        except Exception:
            continue
    return cache


def enrich_open_trades_mtm(
    trades: list[dict[str, Any]],
    client: DhanClient | None,
    *,
    persist: bool = True,
) -> list[dict[str, Any]]:
    if client is None:
        return trades
    from index_ai.learning import is_broker_filled_open

    open_rows = [
        t
        for t in trades
        if t.get("pnl") is None and is_broker_filled_open(t)
    ]
    ltp_cache = _build_ltp_cache(client, open_rows) if open_rows else {}

    out: list[dict[str, Any]] = []
    for trade in trades:
        if trade.get("pnl") is not None:
            out.append(trade)
            continue
        if not is_broker_filled_open(trade):
            out.append(trade)
            continue
        out.append(
            enrich_open_trade_mtm(trade, client, persist=persist, ltp_cache=ltp_cache)
        )
    return out
