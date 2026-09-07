"""Dhan order placement helpers — normalize responses and validate live fills."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from index_ai.config import AppSettings
from index_ai.dhan import DhanClient

_REJECTED_STATUSES = frozenset({"REJECTED", "CANCELLED", "EXPIRED"})
_FILLED_STATUSES = frozenset({"TRADED", "PART_TRADED"})
_PENDING_STATUSES = frozenset({"PENDING", "TRANSIT"})


_VALID_PRODUCT_TYPES = frozenset({"INTRADAY", "MARGIN", "CNC"})


def order_product_type() -> str:
    raw = os.getenv("DHAN_ORDER_PRODUCT_TYPE", "INTRADAY").strip().upper()
    if raw not in _VALID_PRODUCT_TYPES:
        return "INTRADAY"
    return raw


def order_product_type_for_leg(*, transaction_type: str, exchange_segment: str) -> str:
    """Short F&O legs usually need MARGIN (NRML); long legs use DHAN_ORDER_PRODUCT_TYPE."""
    tx = str(transaction_type or "").upper()
    seg = str(exchange_segment or "").upper()
    if seg == "NSE_FNO" and tx == "SELL":
        raw = os.getenv("DHAN_SELL_PRODUCT_TYPE", "MARGIN").strip().upper()
        if raw in _VALID_PRODUCT_TYPES:
            return raw
        return "MARGIN"
    return order_product_type()


def build_market_order_payload(
    *,
    client_id: str,
    security_id: int,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    correlation_id: str,
    product_type: str,
) -> dict[str, Any]:
    """Minimal Dhan v2 MARKET body (omit empty optional fields — avoids DH-905)."""
    qty = int(quantity)
    if qty < 1:
        raise ValueError(f"Order quantity must be >= 1 (got {qty})")
    sid = int(security_id)
    if sid < 1:
        raise ValueError(f"Invalid securityId {security_id}")
    cid = str(client_id).strip()
    if not cid:
        raise ValueError("DHAN_CLIENT_ID is required for order placement.")
    corr = str(correlation_id or "")[:30]
    payload: dict[str, Any] = {
        "dhanClientId": cid,
        "transactionType": str(transaction_type).upper(),
        "exchangeSegment": str(exchange_segment).upper(),
        "productType": str(product_type).upper(),
        "orderType": "MARKET",
        "validity": "DAY",
        "securityId": str(sid),
        "quantity": qty,
        "afterMarketOrder": False,
    }
    if corr:
        payload["correlationId"] = corr
    return payload


def _order_id_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_order_response(data: Any) -> dict[str, Any]:
    """Flatten Dhan order API payloads (top-level, nested data, or single-row list)."""
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict):
            return normalize_order_response(data[0])
        return {"raw": data}
    if not isinstance(data, dict):
        return {"raw": data}
    out = dict(data)
    if not out.get("orderStatus"):
        for alt in ("order_status", "OrderStatus", "status"):
            if out.get(alt):
                out["orderStatus"] = out[alt]
                break
    if not out.get("orderId"):
        for alt in ("order_id", "OrderId", "orderNo"):
            if out.get(alt):
                out["orderId"] = out[alt]
                break
    if out.get("orderId") or out.get("orderStatus"):
        return out
    inner = data.get("data")
    if isinstance(inner, dict):
        return normalize_order_response(inner)
    return out


def build_order_book_index(client: DhanClient) -> dict[str, dict[str, Any]]:
    """Map orderId -> latest row from GET /orders (one call per sync batch)."""
    index: dict[str, dict[str, Any]] = {}
    try:
        for row in client.list_today_orders():
            parsed = normalize_order_response(row)
            oid = _order_id_str(parsed.get("orderId"))
            if oid:
                index[oid] = parsed
    except Exception:
        pass
    return index


def build_trade_fill_index(client: DhanClient) -> dict[str, dict[str, Any]]:
    """Map orderId -> latest fill row from GET /trades (authoritative for executed orders)."""
    index: dict[str, dict[str, Any]] = {}
    try:
        for row in client.list_today_trades():
            if not isinstance(row, dict):
                continue
            oid = _order_id_str(row.get("orderId") or row.get("order_id"))
            if oid:
                index[oid] = row
    except Exception:
        pass
    return index


def _merge_trade_fill(order_row: dict[str, Any], fill_row: dict[str, Any]) -> dict[str, Any]:
    """Upgrade order snapshot when trade book shows an executed fill."""
    merged = dict(order_row)
    merged.update(fill_row)
    merged["orderStatus"] = "TRADED"
    merged["_trade_book_confirmed"] = True
    if fill_row.get("tradedPrice") is not None:
        merged["tradedPrice"] = fill_row["tradedPrice"]
        merged["averageTradedPrice"] = fill_row.get("averageTradedPrice") or fill_row["tradedPrice"]
    if fill_row.get("tradedQuantity") is not None:
        merged["filledQty"] = fill_row["tradedQuantity"]
        merged["tradedQuantity"] = fill_row["tradedQuantity"]
    return merged


def fetch_order_status(
    client: DhanClient,
    order_id: str,
    *,
    book_index: dict[str, dict[str, Any]] | None = None,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read latest status from order book, trade book, then GET /orders/{id}."""
    oid = _order_id_str(order_id)
    if not oid:
        return {"orderStatus": "UNKNOWN", "lookup_error": "empty order id"}

    if trade_fill_index and oid in trade_fill_index:
        base = dict(book_index.get(oid) if book_index and oid in book_index else {"orderId": oid})
        return normalize_order_response(_merge_trade_fill(base, trade_fill_index[oid]))

    if book_index and oid in book_index:
        parsed = dict(book_index[oid])
        if trade_fill_index and oid in trade_fill_index:
            parsed = _merge_trade_fill(parsed, trade_fill_index[oid])
        if effective_order_status(parsed) in _FILLED_STATUSES:
            return parsed
        # Order book row may lag — still check trade book below before returning.

    errors: list[str] = []
    try:
        parsed = normalize_order_response(client.get_order(oid))
        if trade_fill_index and oid in trade_fill_index:
            parsed = normalize_order_response(_merge_trade_fill(parsed, trade_fill_index[oid]))
        status = effective_order_status(parsed)
        if status and status != "UNKNOWN":
            return parsed
        errors.append("get_order returned no status")
    except Exception as exc:
        errors.append(f"get_order: {exc}")

    try:
        fills = client.trades_for_order(oid)
        if fills:
            fill = fills[0] if isinstance(fills[0], dict) else {}
            base = {"orderId": oid}
            if book_index and oid in book_index:
                base = dict(book_index[oid])
            return normalize_order_response(_merge_trade_fill(base, fill))
        errors.append("no fills in trade book for order")
    except Exception as exc:
        errors.append(f"trades_for_order: {exc}")

    if book_index and oid in book_index:
        parsed = dict(book_index[oid])
        if effective_order_status(parsed) != "UNKNOWN":
            return parsed

    if book_index is None:
        try:
            for row in client.list_today_orders():
                parsed = normalize_order_response(row)
                if _order_id_str(parsed.get("orderId")) == oid:
                    return parsed
            errors.append("order id not in today's order book")
        except Exception as exc:
            errors.append(f"list_orders: {exc}")
    else:
        errors.append("order id not in today's order book")

    return {
        "orderId": oid,
        "orderStatus": "UNKNOWN",
        "lookup_errors": errors,
    }


def _cached_broker_leg_rows(option: dict[str, Any]) -> list[dict[str, Any]]:
    """Last placement poll responses saved on the journal option."""
    payload = option.get("broker_orders")
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("legs") or []:
        if not isinstance(item, dict):
            continue
        resp = item.get("response")
        if isinstance(resp, dict):
            rows.append(normalize_order_response(resp))
    single = payload.get("response")
    if isinstance(single, dict):
        rows.append(normalize_order_response(single))
    return rows


def order_response_ok(response: dict[str, Any]) -> bool:
    """True only when Dhan already reports a non-rejected acceptance (not a final fill)."""
    parsed = normalize_order_response(response)
    status = str(parsed.get("orderStatus") or "").upper()
    if status in _REJECTED_STATUSES:
        return False
    return status in _FILLED_STATUSES or status in _PENDING_STATUSES or bool(parsed.get("orderId"))


def order_rejection_detail(parsed: dict[str, Any]) -> str:
    parts: list[str] = []
    oms = _oms_rejection_text(parsed)
    if oms:
        parts.append(oms)
    for key in ("remarks", "reasonDescription", "message"):
        val = parsed.get(key)
        if val:
            parts.append(str(val).strip())
    status = order_status_label(parsed)
    if status and status != "UNKNOWN" and status in _REJECTED_STATUSES:
        parts.append(status)
    return " — ".join(dict.fromkeys(parts)) or "REJECTED"


def _confirm_wait_seconds() -> float:
    try:
        return max(2.0, min(30.0, float(os.getenv("DHAN_ORDER_CONFIRM_SEC", "10"))))
    except ValueError:
        return 10.0


def wait_for_order_terminal(
    client: DhanClient,
    order_id: str,
    *,
    timeout_sec: float | None = None,
    poll_sec: float = 0.45,
) -> dict[str, Any]:
    """Poll GET /orders until TRADED, REJECTED, or timeout (still PENDING)."""
    deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else _confirm_wait_seconds())
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        raw = client.get_order(str(order_id))
        last = normalize_order_response(raw)
        status = str(last.get("orderStatus") or "").upper()
        if status in _FILLED_STATUSES or status in _REJECTED_STATUSES:
            return last
        time.sleep(poll_sec)
    return last


def resolve_leg_broker_status(
    order_id: str,
    parsed: dict[str, Any],
    *,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
    strict_trade_book: bool = True,
) -> str:
    """Status for journal sync — TRADED only with trade-book fill or filledQty proof."""
    oid = _order_id_str(order_id)
    row = normalize_order_response(parsed) if parsed else {}
    raw = str(row.get("orderStatus") or row.get("status") or "").upper().strip()
    aliases = {
        "SUCCESS": "TRADED",
        "SUCCESSFUL": "TRADED",
        "COMPLETE": "TRADED",
        "COMPLETED": "TRADED",
        "EXECUTED": "TRADED",
        "FILLED": "TRADED",
        "TRADE": "TRADED",
    }
    raw = aliases.get(raw, raw)

    if raw in _REJECTED_STATUSES:
        return raw
    if _oms_rejection_text(row):
        return "REJECTED"

    fill_row = (trade_fill_index or {}).get(oid) if oid else None
    if fill_row and _filled_quantity(fill_row) > 0:
        return "TRADED"

    filled = _filled_quantity(row)
    qty = int(row.get("quantity") or 0)
    if filled > 0 and (qty <= 0 or filled >= qty):
        if strict_trade_book and not row.get("_trade_book_confirmed"):
            return "REJECTED"
        return "TRADED"
    if filled > 0:
        return "PART_TRADED"

    if raw in _FILLED_STATUSES:
        if strict_trade_book and trade_fill_index is not None and oid not in trade_fill_index:
            return "REJECTED"
        return raw

    if raw in _PENDING_STATUSES:
        return raw
    return raw or "UNKNOWN"


def aggregate_order_status_from_statuses(
    statuses: list[str],
    leg_results: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """Return journal status from pre-resolved leg statuses."""
    if any(s in _REJECTED_STATUSES for s in statuses):
        reasons = [
            order_rejection_detail(r)
            for r, s in zip(leg_results, statuses, strict=False)
            if s in _REJECTED_STATUSES
        ]
        if not reasons and any(
            s in _FILLED_STATUSES for s in statuses
        ) and not all(s in _FILLED_STATUSES for s in statuses):
            reasons.append("Partial fill — not all spread legs executed on Dhan")
        return "LIVE_REJECTED", "; ".join(reasons) or "Order rejected by Dhan"
    if statuses and all(s == "TRADED" for s in statuses):
        return "LIVE_TRADED", None
    if len(statuses) > 1 and any(s in _FILLED_STATUSES for s in statuses):
        return (
            "LIVE_REJECTED",
            "Partial fill — not all spread legs executed on Dhan",
        )
    if any(s in _FILLED_STATUSES for s in statuses) and any(
        s not in _FILLED_STATUSES for s in statuses
    ):
        return (
            "LIVE_REJECTED",
            "Partial fill — not all spread legs executed on Dhan",
        )
    if any(s in _PENDING_STATUSES for s in statuses):
        return "LIVE_PENDING", None
    if any(s == "UNKNOWN" for s in statuses):
        return "LIVE_PENDING", None
    return "LIVE_SENT", None


def aggregate_order_status(leg_results: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Return journal status at placement time (order API only, not strict trade book)."""
    statuses = [effective_order_status(r) for r in leg_results]
    return aggregate_order_status_from_statuses(statuses, leg_results)


def confirm_placed_orders(
    client: DhanClient,
    responses: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Poll each order id until terminal; raise if any leg rejected."""
    trade_fill_index = build_trade_fill_index(client)
    confirmed: list[dict[str, Any]] = []
    order_ids: list[str] = []
    for item in responses:
        parsed = item.get("response") if isinstance(item.get("response"), dict) else normalize_order_response(item)
        oid = _order_id_str(parsed.get("orderId"))
        order_ids.append(oid)
        if not oid:
            confirmed.append(parsed)
            continue
        final = wait_for_order_terminal(client, str(oid))
        confirmed.append(final)
    statuses = [
        resolve_leg_broker_status(
            oid,
            row,
            trade_fill_index=trade_fill_index,
            strict_trade_book=True,
        )
        for oid, row in zip(order_ids, confirmed, strict=False)
    ]
    status, reason = aggregate_order_status_from_statuses(statuses, confirmed)
    if status == "LIVE_REJECTED":
        raise RuntimeError(f"Dhan rejected order: {reason}")
    return status, confirmed, reason


def _filled_quantity(parsed: dict[str, Any]) -> int:
    for key in ("filledQty", "filled_qty", "tradedQuantity", "traded_quantity", "closedQuantity"):
        val = parsed.get(key)
        if val is None:
            continue
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            continue
    return 0


def _oms_rejection_text(parsed: dict[str, Any]) -> str | None:
    desc = str(parsed.get("omsErrorDescription") or "").strip()
    if desc:
        return desc
    code = str(parsed.get("omsErrorCode") or "").strip()
    if code and code not in {"0", "00", "000", "0000"}:
        return code
    return None


def effective_order_status(parsed: dict[str, Any]) -> str:
    """Map Dhan order row to a normalized status string."""
    status = str(parsed.get("orderStatus") or parsed.get("status") or "").upper().strip()
    aliases = {
        "SUCCESS": "TRADED",
        "SUCCESSFUL": "TRADED",
        "COMPLETE": "TRADED",
        "COMPLETED": "TRADED",
        "EXECUTED": "TRADED",
        "FILLED": "TRADED",
        "TRADE": "TRADED",
    }
    status = aliases.get(status, status)
    qty = int(parsed.get("quantity") or parsed.get("tradedQuantity") or 0)
    filled = _filled_quantity(parsed)
    if filled > 0 and (qty <= 0 or filled >= qty):
        return "TRADED"
    if filled > 0:
        return "PART_TRADED"
    if status in _REJECTED_STATUSES | _FILLED_STATUSES | _PENDING_STATUSES:
        return status
    if _oms_rejection_text(parsed):
        return "REJECTED"
    return status or "UNKNOWN"


def order_status_label(response: dict[str, Any]) -> str:
    return effective_order_status(normalize_order_response(response))


def _leg_identity(leg: dict[str, Any]) -> tuple[int, str, str]:
    opt = str(leg.get("option_type") or "").upper()
    if opt == "CE":
        opt = "CALL"
    elif opt == "PE":
        opt = "PUT"
    return (
        int(leg.get("security_id") or 0),
        str(leg.get("transaction_type") or "").upper(),
        opt,
    )


def _entry_leg_sort_key(leg: dict[str, Any]) -> tuple[int, int, float]:
    """
    Live entry order for credit / hedged sells:
    1) All BUY legs (hedge) before any SELL
    2) Within each side: calls before puts (buy calls, then buy puts, then sell calls, then sell puts)
  """
    tx = str(leg.get("transaction_type") or "").upper()
    opt = str(leg.get("option_type") or "").upper()
    if opt == "CE":
        opt = "CALL"
    elif opt == "PE":
        opt = "PUT"
    tx_rank = 0 if tx == "BUY" else 1
    opt_rank = 0 if opt == "CALL" else 1 if opt == "PUT" else 2
    return (tx_rank, opt_rank, float(leg.get("strike") or 0))


def _entry_leg_sequence(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Long (hedge) legs first, then shorts — required for spread margin on Dhan."""
    return sorted(legs, key=_entry_leg_sort_key)


def _exit_leg_sequence(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Close shorts (cover SELL legs) before selling long hedges (BUY legs)."""
    shorts = [leg for leg in legs if str(leg.get("transaction_type") or "").upper() == "SELL"]
    longs = [leg for leg in legs if str(leg.get("transaction_type") or "").upper() == "BUY"]
    return shorts + longs


def _wait_hedge_before_short_legs(
    client: DhanClient,
    sequenced: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    current_index: int,
) -> None:
    """After each BUY fill, wait before sending SELL legs so margin sees the hedge."""
    leg = sequenced[current_index]
    if str(leg.get("transaction_type") or "").upper() != "BUY":
        return
    if not any(
        str(l.get("transaction_type") or "").upper() == "SELL" for l in sequenced[current_index + 1 :]
    ):
        return
    oid = _order_id_str((responses[-1].get("response") or {}).get("orderId"))
    if not oid:
        return
    terminal = wait_for_order_terminal(client, oid)
    parsed = normalize_order_response(terminal)
    responses[-1]["response"] = parsed
    responses[-1]["order_status"] = order_status_label(parsed)
    status = effective_order_status(parsed)
    if status in _REJECTED_STATUSES:
        raise RuntimeError(
            f"Hedge leg rejected before short leg: {order_rejection_detail(parsed)}"
        )


def live_orders_enabled(settings: AppSettings) -> bool:
    return (
        settings.dhan.ready
        and settings.risk.trading_mode == "LIVE"
        and settings.risk.allow_live_trading
    )


def place_live_entry_orders(
    client: DhanClient,
    option: dict[str, Any],
    *,
    settings: AppSettings,
    signal_action: str = "",
) -> dict[str, Any]:
    """Send MARKET entry orders to Dhan; raises on hard failure."""
    if not live_orders_enabled(settings):
        raise RuntimeError(
            "Live orders are disabled. Turn on Live mode in the dashboard and ensure Dhan is ready."
        )

    from index_ai.execution_safety import validate_live_order_payload

    instrument_key = str(option.get("instrument") or "NIFTY")
    action = str(signal_action or option.get("action") or "").upper()
    safety = validate_live_order_payload(
        option, instrument_key=instrument_key, action=action, settings=settings
    )
    if not safety.ok:
        raise RuntimeError(f"Live order safety check failed: {safety.reason}")

    legs = list(option.get("legs") or [])
    base_id = uuid.uuid4().hex[:10]
    responses: list[dict[str, Any]] = []

    if legs:
        sequenced = _entry_leg_sequence(legs)
        for idx, leg in enumerate(sequenced):
            leg_tx = str(leg["transaction_type"])
            leg_seg = str(leg["segment"])
            raw = client.place_market_order(
                security_id=int(leg["security_id"]),
                exchange_segment=leg_seg,
                transaction_type=leg_tx,
                quantity=int(leg.get("quantity") or option.get("quantity") or 1),
                correlation_id=f"idxai-{base_id}-{idx}"[:30],
                product_type=order_product_type_for_leg(
                    transaction_type=leg_tx,
                    exchange_segment=leg_seg,
                ),
            )
            parsed = normalize_order_response(raw)
            responses.append({"leg": leg, "response": parsed, "raw": raw})
            if not order_response_ok(raw):
                raise RuntimeError(
                    f"Dhan rejected {leg.get('transaction_type')} {leg.get('option_type')} "
                    f"strike {leg.get('strike')}: {order_rejection_detail(parsed)}"
                )
            _wait_hedge_before_short_legs(client, sequenced, responses, idx)
        status, confirmed, _ = confirm_placed_orders(client, responses)
        for item, final in zip(responses, confirmed, strict=False):
            item["response"] = final
            item["order_status"] = order_status_label(final)
        return {
            "status": status,
            "legs": responses,
            "order_ids": [r["response"].get("orderId") for r in responses if r["response"].get("orderId")],
            "order_statuses": [r.get("order_status") for r in responses],
        }

    single_tx = str(option["transaction_type"])
    single_seg = str(option["segment"])
    raw = client.place_market_order(
        security_id=int(option["security_id"]),
        exchange_segment=single_seg,
        transaction_type=single_tx,
        quantity=int(option.get("quantity") or 1),
        correlation_id=f"idxai-{base_id}"[:30],
        product_type=order_product_type_for_leg(
            transaction_type=single_tx,
            exchange_segment=single_seg,
        ),
    )
    parsed = normalize_order_response(raw)
    if not order_response_ok(raw):
        raise RuntimeError(f"Dhan rejected order: {order_rejection_detail(parsed)}")
    status, confirmed, _ = confirm_placed_orders(client, [{"response": parsed}])
    final = confirmed[0] if confirmed else parsed
    return {
        "status": status,
        "response": final,
        "raw": raw,
        "order_ids": [final.get("orderId")] if final.get("orderId") else [],
        "order_statuses": [order_status_label(final)],
    }


def place_live_exit_orders(
    client: DhanClient,
    option: dict[str, Any],
    *,
    settings: AppSettings,
    quantity: int,
    trade: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Opposite-side MARKET orders to close a live position."""
    if not live_orders_enabled(settings):
        raise RuntimeError("Live exit blocked — not in Live mode or Dhan not ready.")

    if trade is not None:
        from index_ai.execution_safety import validate_live_exit_allowed

        exit_ok = validate_live_exit_allowed(trade, settings)
        if not exit_ok.ok:
            raise RuntimeError(exit_ok.reason)

    legs = list(option.get("legs") or [])
    base_id = uuid.uuid4().hex[:10]
    responses: list[dict[str, Any]] = []

    if legs:
        for idx, leg in enumerate(_exit_leg_sequence(legs)):
            leg_tx = str(leg.get("transaction_type") or "SELL").upper()
            exit_tx = "BUY" if leg_tx == "SELL" else "SELL"
            leg_seg = str(leg["segment"])
            raw = client.place_market_order(
                security_id=int(leg["security_id"]),
                exchange_segment=leg_seg,
                transaction_type=exit_tx,
                quantity=int(leg.get("quantity") or quantity),
                correlation_id=f"idxai-x-{base_id}-{idx}"[:30],
                product_type=order_product_type_for_leg(
                    transaction_type=exit_tx,
                    exchange_segment=leg_seg,
                ),
            )
            parsed = normalize_order_response(raw)
            responses.append({"leg": leg, "exit_side": exit_tx, "response": parsed})
            if not order_response_ok(raw):
                raise RuntimeError(
                    f"Dhan rejected exit {exit_tx} {leg.get('option_type')}: {order_status_label(raw)}"
                )
        return {"legs": responses, "order_ids": [r["response"].get("orderId") for r in responses]}

    exit_side = "SELL" if str(option.get("transaction_type") or "BUY").upper() == "BUY" else "BUY"
    single_seg = str(option["segment"])
    raw = client.place_market_order(
        security_id=int(option["security_id"]),
        exchange_segment=single_seg,
        transaction_type=exit_side,
        quantity=int(quantity),
        correlation_id=f"idxai-x-{base_id}"[:30],
        product_type=order_product_type_for_leg(
            transaction_type=exit_side,
            exchange_segment=single_seg,
        ),
    )
    parsed = normalize_order_response(raw)
    if not order_response_ok(raw):
        raise RuntimeError(f"Dhan rejected exit order: {order_status_label(raw)}")
    return {"response": parsed, "order_ids": [parsed.get("orderId")] if parsed.get("orderId") else []}


def attach_broker_orders(option: dict[str, Any], broker_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not broker_payload:
        return option
    out = dict(option)
    out["broker_orders"] = broker_payload
    out["broker_order_ids"] = broker_payload.get("order_ids") or []
    out["broker_order_statuses"] = broker_payload.get("order_statuses") or []
    legs = out.get("legs") or []
    broker_legs = broker_payload.get("legs") or []
    if legs and broker_legs:
        by_leg = {
            _leg_identity(br.get("leg") or {}): br
            for br in broker_legs
            if isinstance(br.get("leg"), dict)
        }
        merged: list[dict[str, Any]] = []
        for leg in legs:
            row = dict(leg)
            br = by_leg.get(_leg_identity(leg))
            if not br and len(broker_legs) == len(legs):
                br = broker_legs[len(merged)]
            resp = (br or {}).get("response") or {}
            if resp.get("tradedPrice") is not None:
                row["entry_ltp"] = float(resp["tradedPrice"])
            elif resp.get("price") is not None and float(resp.get("price") or 0) > 0:
                row["entry_ltp"] = float(resp["price"])
            if resp.get("orderId"):
                row["broker_order_id"] = resp.get("orderId")
            if br:
                row["broker_order_status"] = br.get("order_status") or order_status_label(resp)
            merged.append(row)
        out["legs"] = merged
        out["order_placement_sequence"] = "BUY_LEGS_FIRST_THEN_SELL"
    return out


def _apply_broker_sync_to_option(
    option: dict[str, Any],
    leg_results: list[dict[str, Any]],
    order_ids: list[str],
    *,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str | None]:
    statuses = [
        resolve_leg_broker_status(
            oid,
            row,
            trade_fill_index=trade_fill_index,
            strict_trade_book=True,
        )
        for oid, row in zip(order_ids, leg_results, strict=False)
    ]
    option["broker_order_statuses"] = statuses
    option["broker_order_poll"] = [
        {
            "order_id": oid,
            "status": st,
            "detail": order_rejection_detail(r) if st in _REJECTED_STATUSES else None,
        }
        for oid, r, st in zip(order_ids, leg_results, statuses, strict=False)
    ]
    return aggregate_order_status_from_statuses(statuses, leg_results)


def _apply_fill_prices_to_legs(
    option: dict[str, Any],
    leg_results: list[dict[str, Any]],
    *,
    order_ids: list[str] | None = None,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Attach fill price from Dhan trade book onto journal legs when confirmed."""
    legs = option.get("legs")
    ids = order_ids or [_order_id_str(x) for x in (option.get("broker_order_ids") or [])]
    if not isinstance(legs, list) or not legs:
        return
    by_id = {_order_id_str(r.get("orderId")): r for r in leg_results if r.get("orderId")}
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        oid = _order_id_str(leg.get("broker_order_id") or (ids[i] if i < len(ids) else ""))
        row = by_id.get(oid) or (leg_results[i] if i < len(leg_results) else None)
        if not isinstance(row, dict):
            continue
        if (
            resolve_leg_broker_status(
                oid,
                row,
                trade_fill_index=trade_fill_index,
                strict_trade_book=True,
            )
            not in _FILLED_STATUSES
        ):
            continue
        fill = (trade_fill_index or {}).get(oid) if oid else None
        px = None
        if fill:
            px = fill.get("tradedPrice") or fill.get("averageTradedPrice")
        if px is None:
            px = row.get("averageTradedPrice") or row.get("tradedPrice") or row.get("traded_price")
        if px is not None:
            try:
                leg["entry_ltp"] = float(px)
            except (TypeError, ValueError):
                pass


def sync_trade_broker_status(
    trade: dict[str, Any],
    client: DhanClient,
    *,
    book_index: dict[str, dict[str, Any]] | None = None,
    trade_fill_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Refresh Dhan order statuses; close journal row if exchange rejected."""
    from index_ai.learning import (
        clear_option_mtm_fields,
        reject_live_trade,
        reopen_live_traded_trade,
        sanitize_rejected_option,
        update_trade_status,
    )

    option = dict(trade.get("option") or {})
    order_ids = [_order_id_str(x) for x in (option.get("broker_order_ids") or []) if _order_id_str(x)]
    if not order_ids:
        return trade

    leg_results: list[dict[str, Any]] = []
    for oid in order_ids:
        final = fetch_order_status(
            client,
            oid,
            book_index=book_index,
            trade_fill_index=trade_fill_index,
        )
        resolved = resolve_leg_broker_status(
            oid,
            final,
            trade_fill_index=trade_fill_index,
            strict_trade_book=True,
        )
        if resolved in {"UNKNOWN", "PENDING", "TRANSIT"}:
            for cached in _cached_broker_leg_rows(option):
                if _order_id_str(cached.get("orderId")) != oid:
                    continue
                cached_resolved = resolve_leg_broker_status(
                    oid,
                    cached,
                    trade_fill_index=trade_fill_index,
                    strict_trade_book=True,
                )
                if cached_resolved in _REJECTED_STATUSES:
                    final = cached
                break
        leg_results.append(final)

    agg_status, reason = _apply_broker_sync_to_option(
        option,
        leg_results,
        order_ids,
        trade_fill_index=trade_fill_index,
    )
    _apply_fill_prices_to_legs(
        option,
        leg_results,
        order_ids=order_ids,
        trade_fill_index=trade_fill_index,
    )
    trade_id = str(trade.get("id") or "")
    prior_status = str(trade.get("status") or "").upper()

    if agg_status == "LIVE_REJECTED" and trade_id:
        option = sanitize_rejected_option(option)
        reject_live_trade(trade_id, reason or "Rejected on Dhan", option=option)
        return {**trade, "status": "LIVE_REJECTED", "pnl": 0.0, "option": option}

    if agg_status != "LIVE_TRADED":
        option = clear_option_mtm_fields(option)
        if prior_status == "LIVE_TRADED" and trade_id and agg_status in {"LIVE_SENT", "LIVE_PENDING"}:
            update_trade_status(trade_id, str(agg_status), option=option)
            return {**trade, "status": agg_status, "pnl": None, "option": option}

    if agg_status == "LIVE_TRADED":
        if prior_status == "LIVE_REJECTED" and trade.get("pnl") is None:
            reopen_live_traded_trade(trade_id, option=option)
            return {**trade, "status": "LIVE_TRADED", "pnl": None, "option": option}
        update_trade_status(trade_id, "LIVE_TRADED", option=option)
        return {**trade, "status": "LIVE_TRADED", "option": option}

    update_trade_status(trade_id, str(agg_status or "LIVE_PENDING"), option=option)
    return {**trade, "status": agg_status, "option": option}


def build_position_index(client: DhanClient) -> dict[int, int]:
    """Map security_id -> net qty from GET /positions (includes carryforward)."""
    index: dict[int, int] = {}
    try:
        for row in client.list_positions():
            if not isinstance(row, dict):
                continue
            sid = int(row.get("securityId") or row.get("security_id") or 0)
            net = int(row.get("netQty") or row.get("net_qty") or 0)
            if sid:
                index[sid] = net
    except Exception:
        pass
    return index


def verify_trade_against_positions(
    trade: dict[str, Any],
    position_index: dict[int, int],
) -> tuple[bool, str | None]:
    """Confirm LIVE_TRADED journal legs still exist on the broker book of record."""
    option = dict(trade.get("option") or {})
    legs = list(option.get("legs") or [])
    trade_qty = int(option.get("quantity") or 0)
    issues: list[str] = []

    if legs:
        for leg in legs:
            sid = int(leg.get("security_id") or 0)
            if not sid:
                continue
            leg_qty = int(leg.get("quantity") or trade_qty or 0)
            tx = str(leg.get("transaction_type") or "BUY").upper()
            expected = leg_qty if tx == "BUY" else -leg_qty
            actual = int(position_index.get(sid) or 0)
            if expected > 0 and actual < expected:
                issues.append(
                    f"BUY {leg.get('strike')} {leg.get('option_type')}: need net ≥{expected}, Dhan has {actual}"
                )
            elif expected < 0 and actual > expected:
                issues.append(
                    f"SELL {leg.get('strike')} {leg.get('option_type')}: need net ≤{expected}, Dhan has {actual}"
                )
        if issues:
            return False, "; ".join(issues)
        return True, None

    sid = int(option.get("security_id") or 0)
    if not sid:
        return True, None
    qty = int(option.get("quantity") or 0)
    tx = str(option.get("transaction_type") or "BUY").upper()
    expected = qty if tx == "BUY" else -qty
    actual = int(position_index.get(sid) or 0)
    if actual == 0:
        return False, f"No broker position for security {sid} (journal expected {expected})"
    if (expected > 0 and actual <= 0) or (expected < 0 and actual >= 0):
        return False, f"Broker position sign mismatch for {sid}: expected {expected}, net {actual}"
    return True, None


def sync_open_live_trades(client: DhanClient) -> int:
    from index_ai.learning import live_trades_for_broker_sync, reject_live_trade

    book_index = build_order_book_index(client)
    trade_fill_index = build_trade_fill_index(client)
    position_index = build_position_index(client)
    updated = 0
    for trade in live_trades_for_broker_sync():
        before_status = str(trade.get("status") or "")
        before_pnl = trade.get("pnl")
        after_trade = sync_trade_broker_status(
            trade,
            client,
            book_index=book_index,
            trade_fill_index=trade_fill_index,
        )
        if str(after_trade.get("status") or "").upper() == "LIVE_TRADED":
            ok, pos_reason = verify_trade_against_positions(after_trade, position_index)
            if not ok:
                trade_id = str(after_trade.get("id") or "")
                option = dict(after_trade.get("option") or {})
                from index_ai.learning import sanitize_rejected_option

                option = sanitize_rejected_option(option)
                reject_live_trade(
                    trade_id,
                    pos_reason or "No matching broker positions for journal legs",
                    option=option,
                )
                after_trade = {
                    **after_trade,
                    "status": "LIVE_REJECTED",
                    "pnl": 0.0,
                    "option": option,
                }
        if (
            str(after_trade.get("status") or "") != before_status
            or after_trade.get("pnl") != before_pnl
            or after_trade.get("option") != trade.get("option")
        ):
            updated += 1
    return updated
