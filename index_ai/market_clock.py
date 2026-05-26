from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE index F&O regular session (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
SQUARE_OFF_TIME = time(15, 25)


def now_ist() -> datetime:
    return datetime.now(IST)


def now_ist_iso() -> str:
    return now_ist().isoformat(timespec="seconds")


def today_ist_date() -> str:
    return now_ist().date().isoformat()


def format_ist_display(value: str | None) -> str | None:
    """Format any stored ISO timestamp for UI (always IST label)."""
    if not value:
        return None
    raw = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    local = dt.astimezone(IST)
    return local.strftime("%d %b %Y, %H:%M:%S IST")


def is_trading_day(when: datetime | None = None) -> bool:
    dt = when or now_ist()
    return dt.weekday() < 5


def is_market_open(when: datetime | None = None) -> bool:
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def is_square_off_window(when: datetime | None = None) -> bool:
    """Last minutes of session — close open intraday positions."""
    dt = when or now_ist()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return SQUARE_OFF_TIME <= t < MARKET_CLOSE


def market_status(when: datetime | None = None) -> dict[str, Any]:
    dt = when or now_ist()
    open_now = is_market_open(dt)
    square_off = is_square_off_window(dt)
    if not is_trading_day(dt):
        phase = "weekend"
        message = "Market closed (weekend)."
    elif dt.time() < MARKET_OPEN:
        phase = "pre_open"
        message = f"Pre-market — opens {MARKET_OPEN.strftime('%H:%M')} IST."
    elif open_now and not square_off:
        phase = "open"
        message = f"Market open until {MARKET_CLOSE.strftime('%H:%M')} IST."
    elif square_off:
        phase = "square_off"
        message = f"Square-off window ({SQUARE_OFF_TIME.strftime('%H:%M')}–{MARKET_CLOSE.strftime('%H:%M')} IST)."
    else:
        phase = "closed"
        message = f"Market closed — session ended {MARKET_CLOSE.strftime('%H:%M')} IST."

    next_open: str | None = None
    if not open_now:
        probe = dt
        for _ in range(8):
            if is_trading_day(probe) and probe.time() < MARKET_OPEN:
                next_dt = probe.replace(
                    hour=MARKET_OPEN.hour,
                    minute=MARKET_OPEN.minute,
                    second=0,
                    microsecond=0,
                )
                next_open = format_ist_display(next_dt.isoformat())
                break
            probe = (probe + timedelta(days=1)).replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0,
            )

    return {
        "timezone": "Asia/Kolkata",
        "now_ist": format_ist_display(dt.isoformat()),
        "now_ist_iso": dt.isoformat(timespec="seconds"),
        "is_open": open_now,
        "is_square_off": square_off,
        "phase": phase,
        "message": message,
        "session": {
            "open": MARKET_OPEN.strftime("%H:%M"),
            "close": MARKET_CLOSE.strftime("%H:%M"),
            "square_off": SQUARE_OFF_TIME.strftime("%H:%M"),
        },
        "next_open_ist": next_open,
    }
