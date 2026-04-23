from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Subset of common NSE closures (verify against official circulars each year).
_NSE_FIXED_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 26),  # Republic Day
        date(2026, 8, 15),  # Independence Day
        date(2026, 10, 2),  # Gandhi Jayanti
    }
)


def _is_nse_holiday(d: date) -> bool:
    return d in _NSE_FIXED_HOLIDAYS_2026


def market_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """
    IST clock + coarse NSE cash-equity style session label (not a compliance feed).
    """
    if now is None:
        now = datetime.now(IST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    d = now.date()
    wd = now.weekday()
    t = now.time()

    if wd >= 5:
        phase, label = "weekend", "Weekend — cash equity typically closed (IST)."
    elif _is_nse_holiday(d):
        phase, label = "holiday", "Likely NSE holiday (partial static list in code — confirm calendar)."
    elif time(9, 0) <= t < time(9, 15):
        phase, label = "pre_open", "Pre-open window (IST) — verify auction rules before acting."
    elif time(9, 15) <= t <= time(15, 30):
        phase, label = "regular", "Regular NSE/BSE cash session window (IST)."
    else:
        phase, label = "after_hours", "Outside regular cash session (IST)."

    return {
        "timezone": "Asia/Kolkata",
        "ist_iso": now.isoformat(timespec="seconds"),
        "ist_date": d.isoformat(),
        "weekday": wd,
        "phase": phase,
        "label": label,
        "universe": "India (NSE/BSE focus; Yahoo symbols often .NS / .BO)",
    }
