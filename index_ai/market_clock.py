from __future__ import annotations

import os
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE index F&O regular session (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
# Algo window: analyse 9:15–9:30 (OI, volume, CPR, EMA), entries 9:30–15:15, flat by 15:15
TRADING_ENTRIES_START = time(9, 30)
TRADING_ENTRIES_END = time(15, 15)
SQUARE_OFF_TIME = time(15, 15)
PRE_OPEN_ANALYSIS_START = time(9, 15)
PRE_OPEN_ANALYSIS_END = time(9, 30)


def _parse_time_env(name: str, default: time) -> time:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt).time()
            return parsed
        except ValueError:
            continue
    return default


def session_times() -> dict[str, time]:
    """Effective session times (env overrides for testing/tuning)."""
    return {
        "market_open": _parse_time_env("MARKET_OPEN_TIME", MARKET_OPEN),
        "market_close": _parse_time_env("MARKET_CLOSE_TIME", MARKET_CLOSE),
        "entries_start": _parse_time_env("TRADING_ENTRIES_START", TRADING_ENTRIES_START),
        "entries_end": _parse_time_env("TRADING_ENTRIES_END", TRADING_ENTRIES_END),
        "square_off": _parse_time_env("SQUARE_OFF_TIME", SQUARE_OFF_TIME),
        "pre_open_start": _parse_time_env("PRE_OPEN_ANALYSIS_START", PRE_OPEN_ANALYSIS_START),
        "pre_open_end": _parse_time_env("PRE_OPEN_ANALYSIS_END", PRE_OPEN_ANALYSIS_END),
    }


def now_ist() -> datetime:
    return datetime.now(IST)


def now_ist_iso() -> str:
    return now_ist().isoformat(timespec="seconds")


def today_ist_date() -> str:
    return now_ist().date().isoformat()


def format_ist_clock_12h(dt: datetime) -> str:
    """12-hour clock for UI, e.g. 3:25:26 PM (no leading zero on hour)."""
    hour = dt.hour % 12 or 12
    am_pm = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d}:{dt.second:02d} {am_pm}"


def format_ist_time_of_day(t: time) -> str:
    """12-hour label for session times, e.g. 9:15 AM."""
    hour = t.hour % 12 or 12
    am_pm = "AM" if t.hour < 12 else "PM"
    return f"{hour}:{t.minute:02d} {am_pm}"


def parse_ist_datetime(value: str | None) -> datetime | None:
    """Parse ISO, Dhan dd/mm/yyyy HH:MM, or naive YYYY-MM-DD HH:MM(:SS) as IST."""
    if not value:
        return None
    text = re.sub(r"\s*IST\s*$", "", str(value).strip(), flags=re.IGNORECASE).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except ValueError:
        pass

    dhan = re.match(
        r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$",
        text,
    )
    if dhan:
        day, month, year, hour, minute, second = dhan.groups()
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or 0),
            tzinfo=IST,
        )

    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(text[:size], fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def format_ist_display(value: str | None) -> str | None:
    """Format any stored ISO or Dhan timestamp for UI (IST, 12-hour clock)."""
    if not value:
        return None
    local = parse_ist_datetime(str(value))
    if local is None:
        return str(value)
    return f"{local.strftime('%d %b %Y')}, {format_ist_clock_12h(local)} IST"


def is_trading_day(when: datetime | None = None) -> bool:
    dt = when or now_ist()
    return dt.weekday() < 5


def is_session_active(when: datetime | None = None) -> bool:
    """Scanner/data window: NSE regular session 9:15–15:30 IST."""
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    times = session_times()
    t = dt.time()
    return times["market_open"] <= t < times["market_close"]


def is_market_open(when: datetime | None = None) -> bool:
    """Alias for session active (charts, scanner cycles)."""
    return is_session_active(when)


def is_pre_open_analysis_window(when: datetime | None = None) -> bool:
    """9:15–9:30 IST — learning/OI/sentiment brief before first entry."""
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    times = session_times()
    t = dt.time()
    return times["pre_open_start"] <= t < times["pre_open_end"]


def is_trading_entries_allowed(when: datetime | None = None) -> bool:
    """New entries only between 9:30 and 15:15 IST."""
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    times = session_times()
    t = dt.time()
    return times["entries_start"] <= t < times["entries_end"]


def is_entry_session_timestamp(when: datetime | None = None) -> bool:
    """True when a new trade entry is allowed (IST weekday + entry window)."""
    return is_trading_entries_allowed(when)


def is_square_off_window(when: datetime | None = None) -> bool:
    """From 15:15 IST — close all open algo positions (until session end)."""
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    times = session_times()
    t = dt.time()
    return times["square_off"] <= t < times["market_close"]


def trading_window_message(when: datetime | None = None) -> str:
    times = session_times()
    dt = when or now_ist()
    if not is_trading_day(dt):
        return "Market closed (weekend)."
    t = dt.time()
    if t < times["pre_open_start"]:
        return f"Pre-open analysis starts {format_ist_time_of_day(times['pre_open_start'])} IST."
    if is_pre_open_analysis_window(dt):
        return (
            f"Pre-open analysis until {format_ist_time_of_day(times['entries_start'])} IST — "
            "no new entries yet."
        )
    if t < times["entries_start"]:
        return f"First entry allowed at {format_ist_time_of_day(times['entries_start'])} IST."
    if t >= times["entries_end"]:
        return (
            f"Entry window closed ({format_ist_time_of_day(times['entries_end'])} IST) — "
            "square-off only."
        )
    return (
        f"Entries allowed until {format_ist_time_of_day(times['entries_end'])} IST; "
        f"flat by {format_ist_time_of_day(times['square_off'])} IST."
    )


def market_status(when: datetime | None = None) -> dict[str, Any]:
    dt = when or now_ist()
    times = session_times()
    session_on = is_session_active(dt)
    pre_open = is_pre_open_analysis_window(dt)
    entries_ok = is_trading_entries_allowed(dt)
    square_off = is_square_off_window(dt)

    if not is_trading_day(dt):
        phase = "weekend"
        message = "Market closed (weekend)."
    elif dt.time() < times["market_open"]:
        phase = "pre_open"
        message = f"Pre-market — session opens {format_ist_time_of_day(times['market_open'])} IST."
    elif pre_open:
        phase = "pre_open_analysis"
        message = (
            f"Pre-open analysis ({format_ist_time_of_day(times['pre_open_start'])}–"
            f"{format_ist_time_of_day(times['pre_open_end'])} IST) — "
            f"first entry at {format_ist_time_of_day(times['entries_start'])} IST."
        )
    elif session_on and entries_ok:
        phase = "open"
        message = (
            f"Trading window open — entries until "
            f"{format_ist_time_of_day(times['entries_end'])} IST."
        )
    elif square_off:
        phase = "square_off"
        message = (
            f"Square-off — close all positions by "
            f"{format_ist_time_of_day(times['square_off'])} IST."
        )
    elif session_on:
        phase = "closed_entries"
        message = f"Entry window closed — session ends {format_ist_time_of_day(times['market_close'])} IST."
    else:
        phase = "closed"
        message = f"Market closed — session ended {format_ist_time_of_day(times['market_close'])} IST."

    next_open: str | None = None
    if not session_on:
        probe = dt
        for _ in range(8):
            if is_trading_day(probe) and probe.time() < times["market_open"]:
                next_dt = probe.replace(
                    hour=times["market_open"].hour,
                    minute=times["market_open"].minute,
                    second=0,
                    microsecond=0,
                )
                next_open = format_ist_display(next_dt.isoformat())
                break
            probe = (probe + timedelta(days=1)).replace(
                hour=times["market_open"].hour,
                minute=times["market_open"].minute,
                second=0,
                microsecond=0,
            )

    return {
        "timezone": "Asia/Kolkata",
        "now_ist": format_ist_display(dt.isoformat()),
        "now_ist_iso": dt.isoformat(timespec="seconds"),
        "is_open": session_on,
        "is_square_off": square_off,
        "is_pre_open_analysis": pre_open,
        "entries_allowed": entries_ok,
        "phase": phase,
        "message": message,
        "session": {
            "open": format_ist_time_of_day(times["market_open"]),
            "close": format_ist_time_of_day(times["market_close"]),
            "entries_start": format_ist_time_of_day(times["entries_start"]),
            "entries_end": format_ist_time_of_day(times["entries_end"]),
            "pre_open_analysis": (
                f"{format_ist_time_of_day(times['pre_open_start'])}–"
                f"{format_ist_time_of_day(times['pre_open_end'])}"
            ),
            "square_off": format_ist_time_of_day(times["square_off"]),
        },
        "next_open_ist": next_open,
    }
