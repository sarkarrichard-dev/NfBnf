"""Shared NSE/MCX trading-holiday calendar.

Richard, 2026-09-14: "i also need a system so that it knows when markets are
closed or are on holidays so that the indian, futures and commodities dont
keep running and occupy computing space and slow down the system." A plain
weekday check (what ``index_ai.market_clock`` and ``commodities.session`` did
before this) treats every Monday-Friday as tradable, which is wrong on an
actual holiday — today, 2026-09-14, is one: Ganesh Chaturthi closes NSE/BSE/F&O
completely, and without this fix the Indian-index and stock-futures scanner
would have kept polling all day for candles that never update.

Source: NSE & MCX circulars for calendar year 2026, cross-checked 2026-09-14
against Zerodha's and Groww's published 2026 holiday calendars (both agree on
all 16 NSE dates and all 4 MCX-full-closure dates below).

MCX does not share NSE's calendar (see ``commodities/session.py``) — most NSE
holidays leave MCX open for a 17:00-23:55 evening session, so only the subset
where MCX is ALSO fully shut belongs in ``MCX_FULL_HOLIDAYS``. November 8
(Diwali Laxmi Pujan Muhurat trading) is deliberately absent from both lists:
it is a special SESSION on a Sunday, not a closure — the market is open, not
shut, so it must not be caught by a holiday check.

This is a hardcoded list, not a live feed. ponytail: it needs a manual refresh
every January against the new year's NSE/MCX circular — a fetched calendar is
more correct but is more than this need justifies today; revisit only if the
yearly refresh is ever missed and causes a real problem. Until it's refreshed,
``*_EXTRA_HOLIDAYS`` env vars patch in a missed date without a code change.
"""

from __future__ import annotations

import os

# Dates NSE/BSE/NFO/BFO are fully closed (no equity, index-option, or
# stock/index-futures trading) — used by both the Indian index-options lane
# and the futures lanes, since both trade the same NSE sessions.
NSE_HOLIDAYS_2026 = frozenset(
    {
        "2026-01-15",  # Municipal Corporation Election - Maharashtra
        "2026-01-26",  # Republic Day
        "2026-03-03",  # Holi
        "2026-03-26",  # Shri Ram Navami
        "2026-03-31",  # Shri Mahavir Jayanti
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-05-28",  # Bakri Id
        "2026-06-26",  # Muharram
        "2026-09-14",  # Ganesh Chaturthi
        "2026-10-02",  # Mahatma Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-10",  # Diwali Balipratipada
        "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
        "2026-12-25",  # Christmas
    }
)

# Dates MCX is closed ALL day (morning AND evening). Every other NSE holiday
# above leaves MCX open for its usual 17:00-23:55 evening session instead.
MCX_FULL_HOLIDAYS_2026 = frozenset(
    {
        "2026-01-26",  # Republic Day
        "2026-04-03",  # Good Friday
        "2026-10-02",  # Mahatma Gandhi Jayanti
        "2026-12-25",  # Christmas
    }
)


def _extra(env_var: str) -> frozenset[str]:
    raw = os.getenv(env_var, "")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def is_nse_holiday(iso_date: str) -> bool:
    return iso_date in NSE_HOLIDAYS_2026 or iso_date in _extra("NSE_EXTRA_HOLIDAYS")


def is_mcx_full_holiday(iso_date: str) -> bool:
    return iso_date in MCX_FULL_HOLIDAYS_2026 or iso_date in _extra("MCX_EXTRA_HOLIDAYS")


if __name__ == "__main__":  # self-check
    assert is_nse_holiday("2026-09-14")  # Ganesh Chaturthi — today, when this was written
    assert not is_nse_holiday("2026-09-15")
    assert is_mcx_full_holiday("2026-01-26")  # Republic Day — MCX also fully shut
    assert not is_mcx_full_holiday("2026-09-14")  # MCX still runs its evening session
    os.environ["NSE_EXTRA_HOLIDAYS"] = "2027-01-01"
    assert is_nse_holiday("2027-01-01")
    del os.environ["NSE_EXTRA_HOLIDAYS"]
    print("index_ai.market_holidays self-check ok")
