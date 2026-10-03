"""Read-only ages of the data the bot trades on, for the dashboard's Data health panel.

Nothing here writes a file or calls the network. Every verdict word (ok / slow /
stale / closed / off / none) is decided here, on the server, from the server's
own clock and the NSE holiday calendar -- the browser only maps a word to a
colour. Module attributes (market_clock, tick_feed ...) are always read at call
time so tests can patch them.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from index_ai import instruments, market_clock, market_log, tick_feed
from index_ai.market_context import spread_calib

STATUSES = ("ok", "slow", "stale", "closed", "off", "none")

# (amber after, red after) in seconds. Live prices are D-03's locked numbers; the
# chain numbers come from measured gaps (median 56-58 s, max 181-227 s); the
# spread numbers are a judgement call -- all four rows are tunable here.
THRESHOLDS = {"ticks": (60, 300), "chain": (300, 600), "spread": (600, 1800)}
OPEN_GRACE_SECONDS = 180  # right after the 09:15 bell nothing goes past amber
CHAIN_LOOKBACK_SESSIONS = 3  # an index that failed all day shows its real age
SPREAD_TAIL_BYTES = 262_144  # ~256 KB holds well over the 30-minute red line


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


def chain_last_seen(keys: tuple[str, ...]) -> dict[str, str | None]:
    """Newest option-chain snapshot time per index, or None. Read-only.

    Opens the 7 GB market log through a ``mode=ro`` URI (so a missing file is an
    error, never created) and only seeks on the covering index
    idx_chain_session (session, instrument, ts). No row counting, and the
    price-tick table is never touched (an unindexed scan of it takes ~30 s).
    """
    found: dict[str, str | None] = {k: None for k in keys}
    try:
        uri = market_log.DB_PATH.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            sessions: list[str] = []
            row = db.execute("SELECT MAX(session) FROM chain").fetchone()
            while row and row[0] and len(sessions) < CHAIN_LOOKBACK_SESSIONS:
                sessions.append(row[0])
                row = db.execute(
                    "SELECT MAX(session) FROM chain WHERE session < ?", (row[0],)
                ).fetchone()
            for key in keys:
                for session in sessions:  # newest first; first reading wins
                    ts = db.execute(
                        "SELECT MAX(ts) FROM chain WHERE session = ? AND instrument = ?",
                        (session, key),
                    ).fetchone()[0]
                    if ts:
                        found[key] = ts
                        break
    except (sqlite3.Error, OSError):
        return {k: None for k in keys}
    return found


def spread_last_seen(keys: tuple[str, ...]) -> dict[str, str | None]:
    """Newest measured-spread sample time per index, from the tail of the samples file."""
    found: dict[str, str | None] = {k: None for k in keys}
    path = spread_calib.SAMPLES_PATH
    try:
        if not path.is_file():
            return found
        with path.open("rb") as fh:
            size = path.stat().st_size
            start = max(0, size - SPREAD_TAIL_BYTES)
            fh.seek(start)
            lines = fh.read().decode("utf-8", "replace").splitlines()
        if start > 0:
            lines = lines[1:]  # the first line of a mid-file read is a fragment
        for line in lines:  # file order is time order, so the last one wins
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("instrument") in found and row.get("at"):
                found[row["instrument"]] = row["at"]
    except OSError:
        return {k: None for k in keys}
    return found


_NO_READING: dict[str, Any] = {
    "status": "none",
    "age_seconds": None,
    "last_at": None,
    "last_seen": None,
    "by_index": {},
}


def _line(
    last_by_index: dict[str, str | None],
    now: datetime,
    flowing: bool,
    kind: str,
    in_grace: bool,
) -> dict[str, Any]:
    ages: dict[str, int | None] = {}
    oldest: tuple[int, str] | None = None
    for index, ts in last_by_index.items():
        seen = market_clock.parse_ist_datetime(ts)
        ages[index] = None if seen is None else int(max(0, (now - seen).total_seconds()))
        if ages[index] is not None and (oldest is None or ages[index] > oldest[0]):
            oldest = (ages[index], ts)  # type: ignore[assignment]
    age = oldest[0] if oldest else None
    last_at = oldest[1] if oldest else None
    return {
        "status": classify(age, flowing, *THRESHOLDS[kind], in_grace=in_grace),
        "age_seconds": age,
        "last_at": last_at,
        "last_seen": market_clock.format_ist_display(last_at),
        "by_index": ages,
    }


def collect(now: datetime | None = None) -> dict[str, Any]:
    """The whole /api/data-health payload. Never raises."""
    now = now or market_clock.now_ist()
    market_open = market_clock.is_market_open(now)
    square = market_clock.is_square_off_window(now)
    open_t = market_clock.session_times()["market_open"]
    bell = now.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    in_grace = market_open and now < bell + timedelta(seconds=OPEN_GRACE_SECONDS)
    as_of = now.isoformat(timespec="seconds")
    ticks, feed = _ticks_and_feed(market_open, in_grace)
    try:
        keys = instruments.configured_index_keys()
    except Exception:
        keys = ()
    # The scanner stops recording the chain at the 15:10 square-off while the
    # market stays open to 15:30, so that window is neutral, not red.
    try:
        chain = _line(chain_last_seen(keys), now, market_open and not square, "chain", in_grace)
    except Exception:
        chain = dict(_NO_READING)
    try:
        spread = _line(spread_last_seen(keys), now, market_open, "spread", in_grace)
    except Exception:
        spread = dict(_NO_READING)
    return {
        "as_of": as_of,
        "as_of_display": market_clock.format_ist_display(as_of),
        "market_open": market_open,
        "square_off_window": square,
        "ticks": ticks,
        "feed": feed,
        "chain": chain,
        "spread": spread,
    }
