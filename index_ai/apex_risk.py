"""Apex Pivot-Trend session limits (max trades/day, entry cut-off)."""

from __future__ import annotations

import os
from datetime import time

from index_ai.learning import connect
from index_ai.market_clock import now_ist, today_ist_date
from index_ai.strategy_params import get_strategy_params


def _parse_time(name: str, default: time) -> time:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            from datetime import datetime

            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return default


def apex_entry_times() -> dict[str, time]:
    return {
        "start": _parse_time("APEX_ENTRIES_START", time(9, 16)),
        "no_entry_after": _parse_time("APEX_NO_ENTRY_AFTER", time(15, 0)),
    }


def is_apex_entry_window(when=None) -> bool:
    dt = when or now_ist()
    if dt.weekday() >= 5:
        return False
    t = dt.timetz().replace(tzinfo=None)
    bounds = apex_entry_times()
    return bounds["start"] <= t < bounds["no_entry_after"]


def apex_entry_window_message() -> str:
    bounds = apex_entry_times()
    return (
        f"Apex entries allowed {bounds['start'].strftime('%H:%M')}–"
        f"{bounds['no_entry_after'].strftime('%H:%M')} IST."
    )


def count_today_trades(instrument_key: str, mode: str) -> int:
    today = today_ist_date()
    normalized = str(mode or "PAPER").upper()
    with connect() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS c FROM trades
            WHERE instrument = ? AND substr(created_at, 1, 10) = ?
              AND upper(mode) = ?
            """,
            (instrument_key, today, normalized),
        ).fetchone()
    return int(row["c"] if row else 0)


def apex_daily_trade_limit_reached(instrument_key: str, mode: str) -> bool:
    limit = get_strategy_params().apex_max_trades_per_day
    return count_today_trades(instrument_key, mode) >= limit
