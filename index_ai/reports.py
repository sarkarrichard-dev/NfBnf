"""Export PnL and order reports (CSV) for daily, weekly, monthly, or custom IST ranges."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.analytics import _filter_period, _parse_created_at, _period_stats
from index_ai.dhan import DhanClient
from index_ai.learning import format_trade_for_ui, recent_trades
from index_ai.market_clock import format_ist_display, now_ist_iso
from index_ai.mtm import enrich_open_trades_mtm

IST = ZoneInfo("Asia/Kolkata")

ORDER_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "created_at_ist",
    "instrument",
    "strategy",
    "structure",
    "position",
    "expiry",
    "mode",
    "status",
    "entry_index",
    "entry_premium",
    "exit_premium",
    "exit_index",
    "realized_pnl_inr",
    "open_mtm_inr",
    "display_pnl_inr",
    "result",
    "confidence",
    "quantity",
    "broker_order_ids",
    "signal_reason",
)


def fetch_trades_for_export(limit: int = 5000) -> list[dict[str, Any]]:
    return recent_trades(limit=limit)


def resolve_report_window(
    period: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    when: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """
    Return (start inclusive, end exclusive, label) in IST for the requested period.
    """
    now = when or datetime.now(IST)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = start_today + timedelta(days=1)
    key = (period or "today").strip().lower()

    if key == "custom":
        if not from_date or not to_date:
            raise ValueError("Custom range requires from and to dates (YYYY-MM-DD).")
        start = datetime.strptime(from_date.strip()[:10], "%Y-%m-%d").replace(tzinfo=IST)
        end_day = datetime.strptime(to_date.strip()[:10], "%Y-%m-%d").replace(tzinfo=IST)
        end = end_day + timedelta(days=1)
        if start >= end:
            raise ValueError("From date must be on or before to date.")
        label = f"{from_date[:10]}_to_{to_date[:10]}"
        return start, end, label

    if key == "today":
        return start_today, tomorrow, start_today.strftime("%Y-%m-%d")

    if key == "week":
        start_week = start_today - timedelta(days=start_today.weekday())
        return start_week, tomorrow, f"week_{start_week.strftime('%Y-%m-%d')}"

    if key == "month":
        start_month = start_today.replace(day=1)
        return start_month, tomorrow, start_month.strftime("%Y-%m")

    if key == "all":
        start = datetime(2000, 1, 1, tzinfo=IST)
        return start, tomorrow, "all_time"

    raise ValueError(f"Unknown period {period!r}. Use today, week, month, all, or custom.")


def build_report(
    period: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    client: DhanClient | None = None,
) -> dict[str, Any]:
    start, end, label = resolve_report_window(period, from_date=from_date, to_date=to_date)
    raw = enrich_open_trades_mtm(fetch_trades_for_export(), client)
    filtered_raw = _filter_period(raw, start, end)
    rows = [format_trade_for_ui(t) for t in filtered_raw]
    stats = _period_stats(filtered_raw)

    start_disp = format_ist_display(start.isoformat(timespec="seconds"))
    end_disp = format_ist_display((end - timedelta(seconds=1)).isoformat(timespec="seconds"))

    return {
        "period": period,
        "period_label": label,
        "range_start_ist": start_disp,
        "range_end_ist": end_disp,
        "generated_at_ist": format_ist_display(now_ist_iso()),
        "timezone": "Asia/Kolkata",
        "summary": stats,
        "orders": rows,
        "order_count": len(rows),
    }


def _order_row(t: dict[str, Any]) -> dict[str, Any]:
    pnl = t.get("pnl")
    mtm = t.get("mtm_pnl")
    display = t.get("display_pnl")
    if pnl is not None:
        result = "WIN" if float(pnl) > 0 else "LOSS" if float(pnl) < 0 else "FLAT"
    elif t.get("is_open"):
        result = "OPEN"
    else:
        result = "—"
    legs = t.get("legs_detail") or []
    legs_txt = "; ".join(
        f"{lg.get('label', '')} entry={lg.get('entry_ltp')} exit={lg.get('current_ltp')}"
        for lg in legs
    ) if legs else ""
    position = t.get("position_summary") or t.get("leg_display") or ""
    if legs_txt and len(legs) > 1:
        position = f"{position} | {legs_txt}"

    return {
        "trade_id": t.get("id"),
        "created_at_ist": t.get("created_at_ist"),
        "instrument": t.get("instrument"),
        "strategy": t.get("action"),
        "structure": t.get("structure") or "",
        "position": position,
        "expiry": t.get("expiry") or "",
        "mode": t.get("mode"),
        "status": t.get("status"),
        "entry_index": t.get("entry_index_price"),
        "entry_premium": t.get("entry_option_ltp"),
        "exit_premium": t.get("exit_option_ltp"),
        "exit_index": t.get("exit_index_price"),
        "realized_pnl_inr": pnl if pnl is not None else "",
        "open_mtm_inr": mtm if t.get("is_open") and mtm is not None else "",
        "display_pnl_inr": display if display is not None else "",
        "result": result,
        "confidence": t.get("confidence"),
        "quantity": t.get("quantity"),
        "broker_order_ids": ", ".join(str(x) for x in (t.get("broker_order_ids") or [])),
        "signal_reason": (t.get("signal_reason") or "").replace("\n", " ").strip(),
    }


def report_to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    s = report.get("summary") or {}

    writer.writerow(["Index Options AI — PnL & Orders Report"])
    writer.writerow(["Generated (IST)", report.get("generated_at_ist")])
    writer.writerow(["Period", report.get("period")])
    writer.writerow(["Range start (IST)", report.get("range_start_ist")])
    writer.writerow(["Range end (IST)", report.get("range_end_ist")])
    writer.writerow([])
    writer.writerow(["PnL summary"])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Total trades", s.get("trades", 0)])
    writer.writerow(["Closed", s.get("closed", 0)])
    writer.writerow(["Open", s.get("open", 0)])
    writer.writerow(["Wins", s.get("wins", 0)])
    writer.writerow(["Losses", s.get("losses", 0)])
    win_rate = s.get("win_rate")
    writer.writerow(["Win rate", f"{win_rate * 100:.1f}%" if win_rate is not None else "—"])
    writer.writerow(["Realized PnL (₹)", s.get("pnl_rupees", 0)])
    writer.writerow([])
    writer.writerow(["Breakdown — by index"])
    for k, v in (s.get("by_instrument") or {}).items():
        writer.writerow([k, v])
    writer.writerow([])
    writer.writerow(["Breakdown — by strategy"])
    for k, v in (s.get("by_action") or {}).items():
        writer.writerow([k, v])
    writer.writerow([])
    writer.writerow(["Orders"])
    writer.writerow(list(ORDER_COLUMNS))
    for t in report.get("orders") or []:
        row = _order_row(t)
        writer.writerow([row.get(col, "") for col in ORDER_COLUMNS])
    return buf.getvalue()


def export_filename(report: dict[str, Any]) -> str:
    label = str(report.get("period_label") or "report")
    date_part = datetime.now(IST).strftime("%Y%m%d")
    return f"index_options_ai_{label}_{date_part}.csv"
