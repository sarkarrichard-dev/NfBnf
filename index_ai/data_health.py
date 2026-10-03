"""Read-only ages of the data the bot trades on, for the dashboard's Data health panel.

Nothing here writes a file or calls the network. Every verdict word (ok / slow /
stale / closed / off / none) is decided here, on the server, from the server's
own clock and the NSE holiday calendar -- the browser only maps a word to a
colour. Module attributes (market_clock, tick_feed ...) are always read at call
time so tests can patch them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from index_ai import market_clock, tick_feed

STATUSES = ("ok", "slow", "stale", "closed", "off", "none")

# (amber after, red after) in seconds. Live prices are D-03's locked numbers; the
# chain numbers come from measured gaps (median 56-58 s, max 181-227 s); the
# spread numbers are a judgement call -- all four rows are tunable here.
THRESHOLDS = {"ticks": (60, 300), "chain": (300, 600), "spread": (600, 1800)}
OPEN_GRACE_SECONDS = 180  # right after the 09:15 bell nothing goes past amber


def classify(
    age: float | None, flowing: bool, amber: float, red: float, *, in_grace: bool = False
) -> str:
    """One status word for one data line. ``flowing`` = this data should be arriving now."""
    if not flowing:
        return "closed"
    if age is None:
        return "none"
    if age >= red:
        return "slow" if in_grace else "stale"
    if age >= amber:
        return "slow"
    return "ok"


def feed_status(enabled: bool, connected: bool, stalled: bool, market_open: bool) -> str:
    if not enabled:
        return "off"
    if not market_open:
        return "closed"
    # A dropped connection means stops fall back to the 20-second price check --
    # the same amber the header pill uses.
    return "ok" if connected and not stalled else "slow"


def _ticks_and_feed(market_open: bool, in_grace: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        st = tick_feed.status()
        age = st["seconds_since_last_tick"]
        ticks = {
            "status": "off"
            if not st["enabled"]
            else classify(age, market_open, *THRESHOLDS["ticks"], in_grace=in_grace),
            "age_seconds": age,
        }
        feed = {
            "status": feed_status(st["enabled"], st["connected"], st["stalled"], market_open),
            "enabled": st["enabled"],
            "connected": st["connected"],
            "stalled": st["stalled"],
            "reconnects": st["reconnects"],
        }  # the feed's raw error text is deliberately not copied (jargon, may carry connection details)
        return ticks, feed
    except Exception:
        return (
            {"status": "none", "age_seconds": None},
            {
                "status": "none",
                "enabled": None,
                "connected": None,
                "stalled": None,
                "reconnects": None,
            },
        )


def collect(now: datetime | None = None) -> dict[str, Any]:
    """The whole /api/data-health payload. Never raises."""
    now = now or market_clock.now_ist()
    market_open = market_clock.is_market_open(now)
    open_t = market_clock.session_times()["market_open"]
    bell = now.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    in_grace = market_open and now < bell + timedelta(seconds=OPEN_GRACE_SECONDS)
    as_of = now.isoformat(timespec="seconds")
    ticks, feed = _ticks_and_feed(market_open, in_grace)
    return {
        "as_of": as_of,
        "as_of_display": market_clock.format_ist_display(as_of),
        "market_open": market_open,
        "square_off_window": market_clock.is_square_off_window(now),
        "ticks": ticks,
        "feed": feed,
    }
