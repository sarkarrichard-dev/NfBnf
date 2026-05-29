"""Pick the nearest tradable option expiry from Dhan's expiry list."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %B %Y",
)


def parse_expiry_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    # ISO date prefix (Dhan often returns YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
    head = s[:10]
    for fmt in _FORMATS:
        try:
            return datetime.strptime(head if fmt.startswith("%Y") else s, fmt).date()
        except ValueError:
            continue
    return None


def pick_nearest_expiry(
    expiries: list[str],
    *,
    now: datetime | None = None,
    prefer_weekly: bool = True,
) -> str | None:
    """
    Return the nearest expiry on or after today (IST).

    Dhan's list order is not guaranteed — do not use expiries[0] blindly.
    For intraday index options, prefer the closest weekly/monthly series.
    """
    if not expiries:
        return None
    ref = (now or datetime.now(IST)).date()
    candidates: list[tuple[date, str]] = []
    for raw in expiries:
        d = parse_expiry_date(raw)
        if d is None:
            continue
        if d >= ref:
            candidates.append((d, raw))

    if not candidates:
        # All parsed expiries in the past — use the latest available string.
        parsed = [(d, raw) for raw in expiries if (d := parse_expiry_date(raw)) is not None]
        if parsed:
            parsed.sort(key=lambda x: x[0], reverse=True)
            return parsed[0][1]
        return expiries[0]

    candidates.sort(key=lambda x: x[0])
    if not prefer_weekly or len(candidates) == 1:
        return candidates[0][1]

    nearest_date, nearest_raw = candidates[0]
    # If multiple expiries share the same nearest date, keep first stable sort order.
    same_day = [raw for d, raw in candidates if d == nearest_date]
    return same_day[0] if same_day else nearest_raw


def resolve_trade_expiry(option: dict[str, Any], client: Any, instrument: Any) -> str | None:
    """Use expiry saved on the trade; else nearest from Dhan."""
    stored = str(option.get("expiry") or "").strip()
    if stored:
        return stored
    expiries = client.expiry_list(instrument)
    return pick_nearest_expiry(expiries)
