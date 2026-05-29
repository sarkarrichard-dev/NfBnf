from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from index_ai.config import AppSettings
from index_ai.credit_spread import compute_credit_mtm, is_credit_option
from index_ai.dhan import DhanClient
from index_ai.dhan_errors import DhanRateLimitError
from index_ai.instruments import get_instrument
from index_ai.learning import record_trade_outcome
from index_ai.market_clock import format_ist_display, now_ist_iso


def _parse_ltp_from_feed(raw: dict[str, Any], segment: str, security_id: int) -> float | None:
    data = raw.get("data") or raw
    bucket = data.get(segment) or data.get(segment.lower()) or {}
    sid = str(security_id)
    row = bucket.get(sid) or bucket.get(security_id) or {}
    last = row.get("last_price") or row.get("ltp") or row.get("lastPrice")
    if last is None and bucket:
        first = next(iter(bucket.values()), {})
        last = first.get("last_price") or first.get("ltp") or first.get("lastPrice")
    return float(last) if last is not None else None


def option_ltp(client: DhanClient, option: dict[str, Any]) -> float:
    segment = str(option.get("segment") or "")
    security_id = int(option["security_id"])
    raw = client.ltp(segment, [security_id])
    price = _parse_ltp_from_feed(raw, segment, security_id)
    if price is None:
        raise RuntimeError(f"No option LTP for security {security_id}.")
    return price


def option_ltp_with_retry(client: DhanClient, option: dict[str, Any], *, attempts: int = 4) -> float:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return option_ltp(client, option)
        except (DhanRateLimitError, httpx.HTTPStatusError, RuntimeError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(min(8.0, 1.5 * (2**attempt)))
    raise last_exc or RuntimeError("Could not fetch option LTP.")


def _ltp_from_chain(client: DhanClient, trade: dict[str, Any]) -> float | None:
    from index_ai.options_expiry import resolve_trade_expiry

    option = trade.get("option") or {}
    instrument_key = str(trade.get("instrument") or option.get("instrument") or "")
    if not instrument_key:
        return None
    inst = get_instrument(instrument_key)
    expiry = resolve_trade_expiry(option, client, inst)
    if not expiry:
        return None
    chain = client.option_chain(inst, expiry)
    rows = (chain.get("data") or {}).get("oc") or {}

    legs = list(option.get("legs") or [])
    if legs:
        from index_ai.credit_spread import mark_to_close_debit

        leg_ltps: list[float] = []
        for leg in legs:
            strike = leg.get("strike")
            side = "ce" if str(leg.get("option_type") or "").upper() == "CALL" else "pe"
            row = rows.get(str(strike)) or rows.get(str(int(strike))) if strike is not None else {}
            if not row and strike is not None:
                nearest = min(rows.keys(), key=lambda k: abs(float(k) - float(strike)), default=None)
                row = rows.get(nearest) or {} if nearest is not None else {}
            leg_row = row.get(side) or {}
            ltp = leg_row.get("last_price") or leg_row.get("ltp")
            if ltp is None:
                return None
            leg_ltps.append(float(ltp))
        return mark_to_close_debit(legs, leg_ltps)

    strike = option.get("strike")
    if strike is None:
        return None
    side = "ce" if str(option.get("option_type") or "").upper() in {"CALL", "CE"} else "pe"
    row = rows.get(str(strike)) or rows.get(str(int(strike))) or {}
    leg = row.get(side) or {}
    ltp = leg.get("last_price") or leg.get("ltp")
    return float(ltp) if ltp is not None else None


def estimate_pnl_from_index_move(
    trade: dict[str, Any],
    current_index_price: float,
    *,
    index_sensitivity: float = 0.45,
) -> float | None:
    """Rough PnL when option LTP is unavailable (ATM delta proxy)."""
    signal = trade.get("signal") or {}
    option = trade.get("option") or {}
    entry_index = float(signal.get("price") or 0)
    entry_ltp = float(option.get("ltp") or 0)
    if entry_index <= 0 or entry_ltp <= 0 or current_index_price <= 0:
        return None
    action = str(trade.get("action") or signal.get("action") or "")
    direction = 1.0 if action == "BUY_CALL" else -1.0 if action == "BUY_PUT" else 0.0
    if direction == 0.0:
        return None
    index_delta = (current_index_price - entry_index) * direction
    estimated_exit = max(0.05, entry_ltp + index_delta * index_sensitivity)
    return estimate_pnl_rupees(
        entry_ltp=entry_ltp,
        exit_ltp=estimated_exit,
        quantity=int(option.get("quantity") or 1),
        transaction_type=str(option.get("transaction_type") or "BUY"),
    )


def estimate_pnl_rupees(
    *,
    entry_ltp: float,
    exit_ltp: float,
    quantity: int,
    transaction_type: str,
) -> float:
    qty = max(1, int(quantity))
    entry = max(0.0, float(entry_ltp))
    exit_p = max(0.0, float(exit_ltp))
    if str(transaction_type).upper() == "BUY":
        return round((exit_p - entry) * qty, 2)
    return round((entry - exit_p) * qty, 2)


def close_open_trade(
    trade: dict[str, Any],
    *,
    client: DhanClient | None,
    app_settings: AppSettings,
    reason: str,
    exit_ltp: float | None = None,
    index_price: float | None = None,
) -> dict[str, Any]:
    """Exit an open trade (paper journal or live opposite MARKET order)."""
    trade_id = str(trade.get("id") or "")
    if not trade_id:
        raise ValueError("trade id required")
    if trade.get("pnl") is not None:
        return {"status": "ALREADY_CLOSED", "trade_id": trade_id}

    option = trade.get("option") or {}
    mode = str(trade.get("mode") or "")
    entry_ltp = float(option.get("ltp") or 0)
    qty = int(option.get("quantity") or 1)
    tx = str(option.get("transaction_type") or "BUY").upper()
    exit_side = "SELL" if tx == "BUY" else "BUY"

    resolved_exit_ltp = exit_ltp
    pnl_estimated = False
    broker_response: dict[str, Any] | None = None

    if (
        client is not None
        and app_settings.dhan.ready
        and resolved_exit_ltp is None
        and not is_credit_option(option)
    ):
        try:
            resolved_exit_ltp = option_ltp_with_retry(client, option)
        except Exception:
            try:
                resolved_exit_ltp = _ltp_from_chain(client, trade)
            except Exception:
                resolved_exit_ltp = None

    legs = list(option.get("legs") or [])
    if mode == "LIVE" and app_settings.risk.trading_mode == "LIVE" and client is not None:
        if legs:
            responses: list[dict[str, Any]] = []
            base_id = uuid.uuid4().hex[:10]
            for idx, leg in enumerate(legs):
                leg_tx = str(leg.get("transaction_type") or "SELL").upper()
                leg_exit = "BUY" if leg_tx == "SELL" else "SELL"
                responses.append(
                    client.place_market_order(
                        security_id=int(leg["security_id"]),
                        exchange_segment=str(leg["segment"]),
                        transaction_type=leg_exit,
                        quantity=int(leg.get("quantity") or qty),
                        correlation_id=f"idxai-x-{base_id}-{idx}"[:30],
                    )
                )
            broker_response = {"legs": responses}
        else:
            broker_response = client.place_market_order(
                security_id=int(option["security_id"]),
                exchange_segment=str(option["segment"]),
                transaction_type=exit_side,
                quantity=qty,
                correlation_id=f"idxai-x-{uuid.uuid4().hex[:12]}",
            )

    pnl: float
    if (
        is_credit_option(option)
        and client is not None
        and app_settings.dhan.ready
        and resolved_exit_ltp is None
    ):
        try:
            pnl, close_debit, _ = compute_credit_mtm(option, client)
            resolved_exit_ltp = close_debit
        except Exception:
            pnl = float(option.get("mtm_pnl") or 0)
            resolved_exit_ltp = float(option.get("last_close_debit") or entry_ltp)
            pnl_estimated = pnl == 0
    elif legs and client is not None and app_settings.dhan.ready and resolved_exit_ltp is None:
        leg_pnls: list[float] = []
        for leg in legs:
            try:
                exit_leg_ltp = option_ltp_with_retry(client, leg)
            except Exception:
                exit_leg_ltp = float(leg.get("ltp") or 0)
            leg_pnls.append(
                estimate_pnl_rupees(
                    entry_ltp=float(leg.get("ltp") or 0),
                    exit_ltp=float(exit_leg_ltp),
                    quantity=int(leg.get("quantity") or qty),
                    transaction_type=str(leg.get("transaction_type") or "SELL"),
                )
            )
        pnl = round(sum(leg_pnls), 2)
        resolved_exit_ltp = entry_ltp
    elif resolved_exit_ltp is not None and resolved_exit_ltp > 0:
        pnl = estimate_pnl_rupees(
            entry_ltp=entry_ltp,
            exit_ltp=float(resolved_exit_ltp),
            quantity=qty,
            transaction_type=tx,
        )
    elif index_price is not None:
        estimated = estimate_pnl_from_index_move(trade, float(index_price))
        if estimated is None:
            pnl = 0.0
        else:
            pnl = estimated
            pnl_estimated = True
        resolved_exit_ltp = resolved_exit_ltp or entry_ltp
    else:
        pnl = 0.0
        resolved_exit_ltp = entry_ltp
        pnl_estimated = True

    note = f"{reason} @ {format_ist_display(now_ist_iso())}"
    if pnl_estimated:
        note += " (PnL estimated from index move — option LTP unavailable)"
    from index_ai.learning import save_exit_prices

    save_exit_prices(
        trade_id,
        exit_option_ltp=float(resolved_exit_ltp) if resolved_exit_ltp else None,
        exit_index_price=float(index_price) if index_price is not None else None,
    )
    learned = record_trade_outcome(trade_id, pnl, note=note)
    return {
        "status": "CLOSED",
        "trade_id": trade_id,
        "pnl": pnl,
        "pnl_estimated": pnl_estimated,
        "exit_ltp": resolved_exit_ltp,
        "exit_side": exit_side,
        "broker_response": broker_response,
        "learned": learned,
    }
