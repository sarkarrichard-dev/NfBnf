from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.dhan import DhanClient
from index_ai.learning import format_trade_for_ui, option_leg_fields, recent_trades
from index_ai.mtm import enrich_open_trades_mtm
from index_ai.market_clock import format_ist_display, now_ist_iso
from index_ai.risk_policy import policy_summary

IST = ZoneInfo("Asia/Kolkata")


def _parse_created_at(value: str) -> datetime:
    raw = (value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(IST)


def _period_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closed if float(t["pnl"]) > 0]
    losses = [t for t in closed if float(t["pnl"]) < 0]
    total_pnl = sum(float(t["pnl"]) for t in closed)
    return {
        "trades": len(trades),
        "closed": len(closed),
        "open": len(trades) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "pnl_rupees": round(total_pnl, 2),
        "by_instrument": _count_by(trades, "instrument"),
        "by_action": _count_by(trades, "action"),
        "by_leg": _count_by_leg(trades),
    }


def _count_by_leg(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        key = option_leg_fields(t).get("leg_display") or "—"
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _count_by(trades: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        key = str(t.get(field) or "—")
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _filter_period(trades: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        ts = _parse_created_at(str(t.get("created_at") or ""))
        if start <= ts < end:
            out.append(t)
    return out


def _series_bucket(trades: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        ts = _parse_created_at(str(t.get("created_at") or ""))
        if bucket == "day":
            key = ts.strftime("%Y-%m-%d")
        elif bucket == "week":
            iso = ts.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = ts.strftime("%Y-%m")
        groups[key].append(t)
    series: list[dict[str, Any]] = []
    for key in sorted(groups.keys(), reverse=True):
        stats = _period_stats(groups[key])
        series.append({"period": key, **stats})
    return series


def build_analytics(limit: int = 500, client: DhanClient | None = None) -> dict[str, Any]:
    raw = recent_trades(limit=limit)
    raw = enrich_open_trades_mtm(raw, client)
    rows = [format_trade_for_ui(t) for t in raw]
    open_mtm = sum(float(r["mtm_pnl"]) for r in rows if r.get("is_open") and r.get("mtm_pnl") is not None)
    now = datetime.now(IST)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = start_today - timedelta(days=start_today.weekday())
    start_month = start_today.replace(day=1)
    tomorrow = start_today + timedelta(days=1)

    today_trades = _filter_period(raw, start_today, tomorrow)
    week_trades = _filter_period(raw, start_week, tomorrow)
    month_trades = _filter_period(raw, start_month, tomorrow)

    return {
        "policy": policy_summary(),
        "timezone": "Asia/Kolkata",
        "generated_at": now_ist_iso(),
        "generated_at_ist": format_ist_display(now_ist_iso()),
        "open_mtm_rupees": round(open_mtm, 2),
        "overview": _period_stats(raw),
        "today": _period_stats(today_trades),
        "week": _period_stats(week_trades),
        "month": _period_stats(month_trades),
        "daily_series": _series_bucket(raw, "day")[:31],
        "weekly_series": _series_bucket(raw, "week")[:12],
        "monthly_series": _series_bucket(raw, "month")[:12],
        "trades": rows,
    }
