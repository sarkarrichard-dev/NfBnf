from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.dhan import DhanClient
from index_ai.learning import (
    expand_trades_to_log_rows,
    format_trade_for_ui,
    option_leg_fields,
    recent_trades,
)
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
    from index_ai.learning import is_live_trade

    closed = [t for t in trades if t.get("pnl") is not None]
    open_raw = [t for t in trades if t.get("pnl") is None]
    paper_open = sum(1 for t in open_raw if not is_live_trade(t))
    live_open = sum(
        1
        for t in open_raw
        if is_live_trade(t) and str(t.get("status") or "").upper() == "LIVE_TRADED"
    )
    wins = [t for t in closed if float(t["pnl"]) > 0]
    losses = [t for t in closed if float(t["pnl"]) < 0]
    total_pnl = sum(float(t["pnl"]) for t in closed)
    return {
        "trades": len(trades),
        "closed": len(closed),
        "open": len(open_raw),
        "paper_open": paper_open,
        "live_open": live_open,
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


def _lane_of(trade: dict[str, Any]) -> str:
    """Group a trade for expectancy reporting: strategy_mode, else buy/sell lane."""
    mode = str(trade.get("strategy_mode") or "").strip()
    if mode and mode not in {"wait", "conflict", ""}:
        return mode
    from index_ai.strategies.strategy_router import trade_lane

    lane = trade_lane(str(trade.get("action") or trade.get("signal", {}).get("action") or ""))
    return {"buy": "buy_premium", "sell": "credit_sell"}.get(lane, "other")


def _est_trade_cost_rupees(trade: dict[str, Any]) -> float | None:
    option = trade.get("option") or {}
    inst = str(trade.get("instrument") or option.get("instrument") or "NIFTY")
    qty = int(option.get("quantity") or 0)
    legs = option.get("legs") or []
    has_px = any(float(leg.get("ltp") or 0) > 0 for leg in legs) or float(option.get("ltp") or 0) > 0
    if qty <= 0 or not has_px:
        return None
    try:
        from index_ai.charges import estimate_trade_cost

        return estimate_trade_cost(option, qty, inst).total_rupees
    except Exception:
        return None


def expectancy_by_lane(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-strategy expectancy from closed trades — the honest scorecard.

    expectancy_rupees is the mean realised P&L per closed trade. For LIVE rows
    that is already net of real charges; for PAPER rows est_cost_rupees shows the
    friction those trades would have carried live.
    """
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        if t.get("pnl") is None:
            continue
        buckets[_lane_of(t)].append(t)

    out: list[dict[str, Any]] = []
    for lane, rows in buckets.items():
        pnls = [float(t["pnl"]) for t in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        costs = [c for c in (_est_trade_cost_rupees(t) for t in rows) if c is not None]
        n = len(rows)
        out.append(
            {
                "lane": lane,
                "closed_trades": n,
                "win_rate": round(len(wins) / n, 3) if n else None,
                "avg_win_rupees": round(sum(wins) / len(wins), 0) if wins else 0.0,
                "avg_loss_rupees": round(sum(losses) / len(losses), 0) if losses else 0.0,
                "expectancy_rupees": round(sum(pnls) / n, 0) if n else 0.0,
                "total_pnl_rupees": round(sum(pnls), 0),
                "est_round_trip_cost_rupees": round(sum(costs) / len(costs), 0) if costs else None,
                "verdict": (
                    "profitable" if n and sum(pnls) / n > 0
                    else "break-even" if n and abs(sum(pnls) / n) < 1
                    else "losing"
                ),
            }
        )
    out.sort(key=lambda r: r["total_pnl_rupees"])
    return out


def _count_by(trades: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        key = str(t.get(field) or "—")
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _row_realized_pnl(row: dict[str, Any]) -> float | None:
    for key in ("leg_pnl", "spread_pnl"):
        if row.get(key) is not None:
            return float(row[key])
    if not row.get("is_open") and row.get("display_pnl") is not None:
        return float(row["display_pnl"])
    return None


def _row_open_mtm(row: dict[str, Any]) -> float | None:
    if not row.get("is_open"):
        return None
    if row.get("leg_mtm") is not None:
        return float(row["leg_mtm"])
    if row.get("display_pnl") is not None and int(row.get("leg_index") or 0) == 0:
        return float(row["display_pnl"])
    return None


def build_pnl_index_groups(log_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in log_rows:
        instrument = str(row.get("instrument") or "UNKNOWN").upper()
        group = groups.setdefault(
            instrument,
            {
                "instrument": instrument,
                "leg_rows": 0,
                "open_legs": 0,
                "closed_legs": 0,
                "realized_pnl": 0.0,
                "open_mtm": 0.0,
            },
        )
        group["leg_rows"] += 1
        if row.get("is_open"):
            group["open_legs"] += 1
        else:
            group["closed_legs"] += 1
        realized = _row_realized_pnl(row)
        if realized is not None:
            group["realized_pnl"] += realized
        mtm = _row_open_mtm(row)
        if mtm is not None:
            group["open_mtm"] += mtm

    preferred = {"NIFTY": 0, "BANKNIFTY": 1, "SENSEX": 2}
    out = []
    for group in groups.values():
        realized = round(float(group["realized_pnl"]), 2)
        open_mtm = round(float(group["open_mtm"]), 2)
        out.append(
            {
                **group,
                "realized_pnl": realized,
                "open_mtm": open_mtm,
                "total_pnl": round(realized + open_mtm, 2),
            }
        )
    return sorted(out, key=lambda g: (preferred.get(str(g["instrument"]), 99), str(g["instrument"])))


def _filter_session_entries(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude off-hours / weekend journal rows from session stats."""
    from index_ai.market_clock import is_entry_session_timestamp

    out: list[dict[str, Any]] = []
    for t in trades:
        ts = _parse_created_at(str(t.get("created_at") or ""))
        if is_entry_session_timestamp(ts):
            out.append(t)
    return out


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


def build_analytics(
    limit: int = 500,
    client: DhanClient | None = None,
    *,
    enrich_mtm: bool = True,
) -> dict[str, Any]:
    if client is not None:
        from index_ai.dhan_orders import sync_open_live_trades

        sync_open_live_trades(client)
    from index_ai.learning import repair_rejected_journal_prices

    repair_rejected_journal_prices()
    raw = recent_trades(limit=limit)
    session_raw = _filter_session_entries(raw)
    if enrich_mtm:
        raw = enrich_open_trades_mtm(raw, client)
    rows = [format_trade_for_ui(t) for t in raw]
    log_rows = expand_trades_to_log_rows(rows)
    now = datetime.now(IST)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = start_today - timedelta(days=start_today.weekday())
    start_month = start_today.replace(day=1)
    tomorrow = start_today + timedelta(days=1)

    open_mtm = sum(float(r["mtm_pnl"]) for r in rows if r.get("is_open") and r.get("mtm_pnl") is not None)
    today_open_mtm = sum(
        float(r["mtm_pnl"])
        for r in rows
        if r.get("is_open")
        and r.get("mtm_pnl") is not None
        and _parse_created_at(str(r.get("created_at") or "")) >= start_today
    )
    open_count = sum(1 for r in rows if r.get("is_open"))
    stale_open_count = sum(
        1
        for r in rows
        if r.get("is_open") and _parse_created_at(str(r.get("created_at") or "")) < start_today
    )

    today_trades = _filter_period(session_raw, start_today, tomorrow)
    week_trades = _filter_period(session_raw, start_week, tomorrow)
    month_trades = _filter_period(session_raw, start_month, tomorrow)

    live_rows = [r for r in rows if r.get("is_live")]
    live_open = [r for r in live_rows if r.get("is_open") and r.get("status") == "LIVE_TRADED"]
    live_closed = [r for r in live_rows if not r.get("is_open")]
    paper_rows = [r for r in rows if r.get("is_paper")]
    paper_open = [r for r in paper_rows if r.get("is_open")]
    paper_closed = [r for r in paper_rows if not r.get("is_open")]

    return {
        "policy": policy_summary(),
        "timezone": "Asia/Kolkata",
        "generated_at": now_ist_iso(),
        "generated_at_ist": format_ist_display(now_ist_iso()),
        "open_mtm_rupees": round(open_mtm, 2),
        "today_open_mtm_rupees": round(today_open_mtm, 2),
        "open_positions": open_count,
        "stale_open_positions": stale_open_count,
        "overview": _period_stats(session_raw),
        "expectancy_by_lane": expectancy_by_lane(session_raw),
        "today": _period_stats(today_trades),
        "week": _period_stats(week_trades),
        "month": _period_stats(month_trades),
        "daily_series": _series_bucket(session_raw, "day")[:31],
        "weekly_series": _series_bucket(session_raw, "week")[:12],
        "monthly_series": _series_bucket(session_raw, "month")[:12],
        "trades": rows,
        "log_rows": log_rows,
        "pnl_by_index": build_pnl_index_groups(log_rows),
        "live_trades": live_rows[:40],
        "live_log_rows": expand_trades_to_log_rows(live_rows[:40]),
        "live_summary": {
            "total": len(live_rows),
            "open": len(live_open),
            "closed": len(live_closed),
            "realized_pnl": round(
                sum(float(r["pnl"]) for r in live_closed if r.get("pnl") is not None),
                2,
            ),
            "open_mtm": round(
                sum(float(r["mtm_pnl"]) for r in live_open if r.get("mtm_pnl") is not None),
                2,
            ),
        },
        "paper_summary": {
            "total": len(paper_rows),
            "open": len(paper_open),
            "closed": len(paper_closed),
            "realized_pnl": round(
                sum(float(r["pnl"]) for r in paper_closed if r.get("pnl") is not None),
                2,
            ),
            "open_mtm": round(
                sum(float(r["mtm_pnl"]) for r in paper_open if r.get("mtm_pnl") is not None),
                2,
            ),
        },
    }
