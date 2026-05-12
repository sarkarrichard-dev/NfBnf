"""IST (Asia/Kolkata) calendar helpers for operator-facing date windows."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from trading_ai_engine.security_http import is_plain_yyyy_mm_dd

IST = ZoneInfo("Asia/Kolkata")


def _parse_plain_ist_date(day_yyyy_mm_dd: str) -> date:
    s = day_yyyy_mm_dd.strip()[:10]
    if not is_plain_yyyy_mm_dd(s):
        raise ValueError("expected YYYY-MM-DD for IST calendar day")
    return date.fromisoformat(s)


def ist_calendar_day_start_utc_iso(day_yyyy_mm_dd: str) -> str:
    """First instant of an IST calendar day, as UTC ISO-8601 (for SQL ``created_at`` lower bound)."""
    d = _parse_plain_ist_date(day_yyyy_mm_dd)
    start = datetime.combine(d, time(0, 0, 0), tzinfo=IST)
    return start.astimezone(timezone.utc).isoformat()


def ist_calendar_day_end_utc_iso(day_yyyy_mm_dd: str) -> str:
    """Last instant of an IST calendar day, as UTC ISO-8601 (for SQL ``created_at`` upper bound)."""
    d = _parse_plain_ist_date(day_yyyy_mm_dd)
    end = datetime.combine(d, time(23, 59, 59, 999999), tzinfo=IST)
    return end.astimezone(timezone.utc).isoformat()


def ist_date_window_to_utc_bounds(
    date_from: str | None,
    date_to: str | None,
) -> tuple[str | None, str | None]:
    """
    Map ``YYYY-MM-DD`` to inclusive bounds in UTC for storage keyed in UTC.

    Plain dates (no ``T``) are interpreted as **IST calendar days**. Strings that already
    contain a full ISO time (``T`` or trailing ``Z``) are passed through unchanged.
    """
    def _is_plain_date(s: str) -> bool:
        t = s.strip()
        if len(t) < 10 or "T" in t[:11] or t.endswith("Z"):
            return False
        return is_plain_yyyy_mm_dd(t[:10])

    af: str | None = None
    bt: str | None = None
    if date_from:
        s = date_from.strip()
        if _is_plain_date(s):
            try:
                af = ist_calendar_day_start_utc_iso(s)
            except ValueError as e:
                raise ValueError(f"invalid date_from: {e}") from e
        else:
            af = s
    if date_to:
        s = date_to.strip()
        if _is_plain_date(s):
            try:
                bt = ist_calendar_day_end_utc_iso(s)
            except ValueError as e:
                raise ValueError(f"invalid date_to: {e}") from e
        else:
            bt = s
    return af, bt


def format_utc_iso_in_ist(iso_timestamp: str) -> str:
    """Format a DB UTC timestamp for display in IST (fixed suffix for clarity)."""
    raw = str(iso_timestamp).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
