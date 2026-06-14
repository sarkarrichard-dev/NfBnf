"""Dhan live account snapshot — funds, trade book, positions (portal-aligned)."""

from __future__ import annotations

from typing import Any

from index_ai.dhan import DhanClient
from index_ai.market_clock import format_ist_display, now_ist_iso


def _float_val(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def normalize_fund_limits(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten GET /fundlimit (Dhan typo: availabelBalance)."""
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("data")
    data = inner if isinstance(inner, dict) else raw
    available = _float_val(
        data.get("availabelBalance")
        or data.get("availableBalance")
        or data.get("available_balance")
    )
    return {
        "dhan_client_id": data.get("dhanClientId") or data.get("dhan_client_id"),
        "available_balance": available,
        "sod_limit": _float_val(data.get("sodLimit") or data.get("sod_limit")),
        "collateral_amount": _float_val(data.get("collateralAmount") or data.get("collateral_amount")),
        "receivable_amount": _float_val(
            data.get("receiveableAmount")
            or data.get("receivableAmount")
            or data.get("receivable_amount")
        ),
        "utilized_amount": _float_val(data.get("utilizedAmount") or data.get("utilized_amount")),
        "blocked_payout_amount": _float_val(
            data.get("blockedPayoutAmount") or data.get("blocked_payout_amount")
        ),
        "withdrawable_balance": _float_val(
            data.get("withdrawableBalance") or data.get("withdrawable_balance")
        ),
        "raw": data,
    }


def format_trade_book_row(row: dict[str, Any]) -> dict[str, Any]:
    """One executed fill from GET /trades."""
    strike = row.get("drvStrikePrice") or row.get("drv_strike_price")
    opt_type = str(row.get("drvOptionType") or row.get("drv_option_type") or "").upper()
    tx = str(row.get("transactionType") or row.get("transaction_type") or "").upper()
    qty = row.get("tradedQuantity") or row.get("traded_quantity")
    price = _float_val(row.get("tradedPrice") or row.get("traded_price"))
    symbol = row.get("tradingSymbol") or row.get("customSymbol") or row.get("trading_symbol") or ""
    leg_parts = [tx.capitalize() if tx else "", str(int(strike)) if strike not in (None, 0, "0") else "", opt_type]
    leg_label = " ".join(p for p in leg_parts if p).strip() or str(symbol)
    return {
        "order_id": str(row.get("orderId") or row.get("order_id") or ""),
        "exchange_order_id": str(row.get("exchangeOrderId") or row.get("exchange_order_id") or ""),
        "exchange_trade_id": str(row.get("exchangeTradeId") or row.get("exchange_trade_id") or ""),
        "transaction_type": tx,
        "exchange_segment": str(row.get("exchangeSegment") or row.get("exchange_segment") or ""),
        "product_type": str(row.get("productType") or row.get("product_type") or ""),
        "order_type": str(row.get("orderType") or row.get("order_type") or ""),
        "trading_symbol": str(symbol),
        "security_id": str(row.get("securityId") or row.get("security_id") or ""),
        "traded_quantity": int(qty) if qty is not None else None,
        "traded_price": price,
        "leg_label": leg_label,
        "expiry": row.get("drvExpiryDate") or row.get("drv_expiry_date"),
        "option_type": opt_type or None,
        "strike": _float_val(strike) if strike not in (None, "", 0) else None,
        "create_time": row.get("createTime") or row.get("create_time"),
        "create_time_ist": format_ist_display(
            str(row.get("createTime") or row.get("create_time") or "")
        ),
        "exchange_time": row.get("exchangeTime") or row.get("exchange_time"),
        "exchange_time_ist": format_ist_display(
            str(row.get("exchangeTime") or row.get("exchange_time") or "")
        ),
    }


def format_position_row(row: dict[str, Any]) -> dict[str, Any]:
    strike = row.get("drvStrikePrice") or row.get("drv_strike_price")
    opt_type = str(row.get("drvOptionType") or row.get("drv_option_type") or "").upper()
    net_qty = row.get("netQty") or row.get("net_qty")
    return {
        "trading_symbol": str(row.get("tradingSymbol") or row.get("trading_symbol") or ""),
        "security_id": str(row.get("securityId") or row.get("security_id") or ""),
        "exchange_segment": str(row.get("exchangeSegment") or row.get("exchange_segment") or ""),
        "product_type": str(row.get("productType") or row.get("product_type") or ""),
        "position_type": str(row.get("positionType") or row.get("position_type") or ""),
        "net_qty": int(net_qty) if net_qty is not None else 0,
        "buy_avg": _float_val(row.get("buyAvg") or row.get("buy_avg")),
        "sell_avg": _float_val(row.get("sellAvg") or row.get("sell_avg")),
        "realized_profit": _float_val(row.get("realizedProfit") or row.get("realized_profit")),
        "unrealized_profit": _float_val(row.get("unrealizedProfit") or row.get("unrealized_profit")),
        "leg_label": " ".join(
            p
            for p in [
                str(row.get("positionType") or ""),
                str(int(strike)) if strike not in (None, 0, "0") else "",
                opt_type,
                str(row.get("tradingSymbol") or ""),
            ]
            if p
        ).strip(),
        "expiry": row.get("drvExpiryDate") or row.get("drv_expiry_date"),
    }


def fetch_dhan_account_snapshot(client: DhanClient) -> dict[str, Any]:
    """Funds + today's trade book + open positions from live Dhan trading APIs."""
    errors: list[str] = []
    funds: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    order_count = 0

    try:
        funds = normalize_fund_limits(client.get_fund_limits())
    except Exception as exc:
        errors.append(f"fundlimit: {exc}")

    try:
        trades = [format_trade_book_row(r) for r in client.list_today_trades()]
        trades.sort(
            key=lambda r: str(r.get("exchange_time") or r.get("create_time") or ""),
            reverse=True,
        )
    except Exception as exc:
        errors.append(f"trades: {exc}")

    try:
        raw_positions = client.list_positions()
        positions = [
            format_position_row(r)
            for r in raw_positions
            if int(r.get("netQty") or r.get("net_qty") or 0) != 0
        ]
    except Exception as exc:
        errors.append(f"positions: {exc}")

    try:
        order_count = len(client.list_today_orders())
    except Exception as exc:
        errors.append(f"orders: {exc}")

    return {
        "ok": funds is not None or bool(trades) or bool(positions),
        "updated_at": now_ist_iso(),
        "updated_at_ist": format_ist_display(now_ist_iso()),
        "funds": funds,
        "tradebook": trades,
        "tradebook_count": len(trades),
        "positions": positions,
        "positions_count": len(positions),
        "orders_today_count": order_count,
        "errors": errors,
    }
