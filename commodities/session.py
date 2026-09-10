"""MCX session clock.

MCX runs **09:00–23:30 IST**. Energy and non-agri metals (crude, natural gas,
gold, silver, copper, ...) extend to **23:55 IST while US daylight time is in
effect** — roughly the 2nd Sunday of March to the 1st Sunday of November — so
their evening close tracks the NYMEX/COMEX close. Agri commodities always close
21:30/23:30; we don't trade those.

Trading days: weekdays. MCX's holiday list overlaps the NSE one but is not
identical (MCX often trades on days NSE is shut for a settlement holiday, and
vice versa). v1 uses the plain weekday check and skips a small hard-coded set;
`MCX_EXTRA_HOLIDAYS` (comma-separated YYYY-MM-DD in .env) extends it without a
code change.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

from index_ai.market_clock import IST, now_ist

MCX_OPEN = time(9, 0)
MCX_CLOSE_STD = time(23, 30)
MCX_CLOSE_DST = time(23, 55)  # energy / non-agri metals while US DST is on

# entries stop this long before the close; open positions are squared off at it
SQUAREOFF_BUFFER_MIN = 5
ENTRY_CUTOFF_BUFFER_MIN = 25


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def us_dst_active(when: datetime | None = None) -> bool:
    """US daylight time: 2nd Sunday of March 02:00 → 1st Sunday of November 02:00
    (local US, close enough at IST-day resolution)."""
    d = (when or now_ist()).astimezone(IST).date()
    start = _nth_weekday(d.year, 3, 6, 2)   # 2nd Sunday, March
    end = _nth_weekday(d.year, 11, 6, 1)    # 1st Sunday, November
    return start <= d < end


def _extra_holidays() -> set[str]:
    raw = os.getenv("MCX_EXTRA_HOLIDAYS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def is_mcx_trading_day(when: datetime | None = None) -> bool:
    d = (when or now_ist()).astimezone(IST)
    return d.weekday() < 5 and d.date().isoformat() not in _extra_holidays()


def mcx_close(dst_session: bool, when: datetime | None = None) -> time:
    """The evening close for an instrument — 23:55 for a DST-linked commodity
    while US DST is on, else 23:30."""
    return MCX_CLOSE_DST if (dst_session and us_dst_active(when)) else MCX_CLOSE_STD


def in_mcx_session(dst_session: bool = False, when: datetime | None = None) -> bool:
    now = (when or now_ist()).astimezone(IST)
    if not is_mcx_trading_day(now):
        return False
    return MCX_OPEN <= now.time() < mcx_close(dst_session, now)


def entries_open(dst_session: bool = False, when: datetime | None = None) -> bool:
    """New entries only until ENTRY_CUTOFF_BUFFER_MIN before the close."""
    now = (when or now_ist()).astimezone(IST)
    if not in_mcx_session(dst_session, now):
        return False
    close_dt = now.replace(
        hour=mcx_close(dst_session, now).hour,
        minute=mcx_close(dst_session, now).minute,
        second=0,
        microsecond=0,
    )
    return now < close_dt - timedelta(minutes=ENTRY_CUTOFF_BUFFER_MIN)


def past_squareoff(dst_session: bool = False, when: datetime | None = None) -> bool:
    """True once we're inside the square-off buffer — open positions must close."""
    now = (when or now_ist()).astimezone(IST)
    if not is_mcx_trading_day(now):
        return True
    close = mcx_close(dst_session, now)
    cutoff = now.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0) - timedelta(
        minutes=SQUAREOFF_BUFFER_MIN
    )
    return now >= cutoff or now.time() < MCX_OPEN


def mcx_day(when: datetime | None = None) -> str:
    return (when or now_ist()).astimezone(IST).date().isoformat()


if __name__ == "__main__":  # self-check
    jun_noon = datetime(2026, 6, 1, 12, 0, tzinfo=IST)      # Mon, US DST on
    jun_late = datetime(2026, 6, 1, 23, 40, tzinfo=IST)
    jun_squareoff = datetime(2026, 6, 1, 23, 52, tzinfo=IST)
    dec_late = datetime(2026, 12, 1, 23, 40, tzinfo=IST)    # US DST off
    sat = datetime(2026, 6, 6, 12, 0, tzinfo=IST)

    assert us_dst_active(jun_noon) and not us_dst_active(dec_late)
    assert in_mcx_session(True, jun_noon) and not in_mcx_session(False, sat)
    assert in_mcx_session(True, jun_late)          # 23:40 < 23:55 DST close
    assert not in_mcx_session(True, dec_late)      # 23:40 >= 23:30 std close
    assert entries_open(True, jun_noon) and not entries_open(True, jun_late)
    assert past_squareoff(True, jun_squareoff) and not past_squareoff(True, jun_noon)
    print("commodities.session self-check ok")
