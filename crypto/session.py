"""The 'crypto day' — anchors for a 24/7 venue.

The 6 PM (NY N-Break) lane trades an IST window (default 18:00–23:00) and its
'day' is that window's IST date. The Ichimoku lane runs around the clock and its
'day' is the UTC calendar date (the 'BTC day' the Pine VWAP anchors to).
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timezone

from index_ai.market_clock import IST, now_ist


def _hhmm(raw: str, default: dtime) -> dtime:
    try:
        h, m = raw.strip().split(":")
        return dtime(int(h), int(m))
    except (ValueError, AttributeError):
        return default


def ny_bounds(ny_start: str, ny_end: str) -> tuple[dtime, dtime]:
    return _hhmm(ny_start, dtime(18, 0)), _hhmm(ny_end, dtime(23, 0))


def in_ny_window(ny_start: str, ny_end: str, now: datetime | None = None) -> bool:
    now = now or now_ist()
    start, end = ny_bounds(ny_start, ny_end)
    t = now.astimezone(IST).time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window wraps past midnight


def in_crypto_session(start: str, end: str, now: datetime | None = None) -> bool:
    """The lane-level trading window — new entries fire only inside it (default
    16:00–06:00 IST, wrapping past midnight). Open positions are managed around
    the clock regardless. Same generic HH:MM window check as ``in_ny_window``."""
    return in_ny_window(start, end, now)


def ny_session_date(ny_start: str, ny_end: str, now: datetime | None = None) -> str:
    """The IST date that owns this session. For a wrap-past-midnight window the
    early-morning tail still belongs to the previous calendar date's session."""
    now = (now or now_ist()).astimezone(IST)
    start, end = ny_bounds(ny_start, ny_end)
    if start > end and now.time() < end:
        from datetime import timedelta

        now = now - timedelta(days=1)
    return now.date().isoformat()


def seconds_to_ny_end(ny_end: str, now: datetime | None = None) -> float:
    now = (now or now_ist()).astimezone(IST)
    end = _hhmm(ny_end, dtime(23, 0))
    end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    return (end_dt - now).total_seconds()


def crypto_day(now: datetime | None = None) -> str:
    """UTC calendar date — the Ichimoku lane's journaling day."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def parse_hhmm(raw: str, default: dtime = dtime(0, 0)) -> dtime:
    """Public form of the HH:MM parser the other session helpers use internally."""
    return _hhmm(raw, default)


def session_date_for(anchor: str, now: datetime | None = None) -> str:
    """The IST date that owns the 24h window starting at ``anchor`` — a single
    reset time rather than a start/end pair (e.g. crypto's ORB lane, whose
    'day' rolls over once at the opening-range anchor, not at a window's
    close). Before ``anchor`` on the clock, still belongs to the prior date's
    window."""
    now = (now or now_ist()).astimezone(IST)
    start = _hhmm(anchor, dtime(0, 0))
    d = now.date()
    if now.time() < start:
        from datetime import timedelta

        d -= timedelta(days=1)
    return d.isoformat()


if __name__ == "__main__":  # self-check
    noon = datetime(2026, 9, 7, 12, 0, tzinfo=IST)
    seven_pm = datetime(2026, 9, 7, 19, 0, tzinfo=IST)
    assert not in_ny_window("18:00", "23:00", noon)
    assert in_ny_window("18:00", "23:00", seven_pm)
    assert not in_crypto_session("16:00", "06:00", noon)  # daytime → Indian lanes
    assert in_crypto_session("16:00", "06:00", seven_pm)
    assert in_crypto_session("16:00", "06:00", datetime(2026, 9, 7, 3, 0, tzinfo=IST))  # overnight
    assert ny_session_date("18:00", "23:00", seven_pm) == "2026-09-07"
    assert abs(seconds_to_ny_end("23:00", seven_pm) - 4 * 3600) < 1
    assert crypto_day(datetime(2026, 9, 7, 2, 0, tzinfo=IST)) == "2026-09-06"  # 20:30 UTC prev day

    assert session_date_for("18:00", seven_pm) == "2026-09-07"  # at/after anchor -> today
    assert (
        session_date_for("18:00", noon) == "2026-09-06"
    )  # before anchor -> still yesterday's window
    assert parse_hhmm("18:00") == dtime(18, 0)
    print("crypto.session self-check ok")
