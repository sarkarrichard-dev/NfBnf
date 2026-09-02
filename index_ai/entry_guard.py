"""Brakes on the entry side, so the scanner can't over-trade a chopping index.

Sep 2 2026: BANKNIFTY moved 276 pts (0.48%) all session. The CPR trend bias
flip-flopped, the scanner opened and closed 7 credit spreads, most of them
caught on the wrong side of a wiggle before the signal flipped back, and the
day cost -3,954 — nearly all of it friction and adverse selection, not signal.

Four gates, all measured off the trade journal (so they survive a restart):

  1. re-entry cooldown  — no new entry within N minutes of *any* close for the
     index (wait a candle for confirmation instead of instantly reversing)
  2. chop lockout       — too many round-trips in the last hour → sit the index
     out for a while
  3. no-trend regime    — CPR wide, or price stuck inside the central range →
     a directional credit spread is a coin flip, skip it
  4. daily trade cap    — a hard per-index ceiling, tighter for BANKNIFTY

The exit side of the same problem is handled by letting ``premium_trail`` own
the exit once a spread is open — see ``position_exits.strategy_exit_reason``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from index_ai.market_clock import now_ist, parse_ist_datetime, today_ist_date


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


REENTRY_COOLDOWN_MIN = _int("ENTRY_REENTRY_COOLDOWN_MIN", 4)
CHOP_CLOSES_PER_HOUR = _int("ENTRY_CHOP_CLOSES_PER_HOUR", 3)
CHOP_LOCKOUT_MIN = _int("ENTRY_CHOP_LOCKOUT_MIN", 45)
_DEFAULT_CAP = {"BANKNIFTY": 4, "SENSEX": 4, "NIFTY": 6}


def daily_cap(instrument: str) -> int:
    key = str(instrument or "").strip().upper()
    return max(1, _int(f"DAILY_TRADE_CAP_{key}", _DEFAULT_CAP.get(key, 6)))


# statuses that never became a real position — must not count toward the cap
_UNFILLED = {"LIVE_REJECTED", "LIVE_SENT", "LIVE_PENDING", "BLOCKED"}


def _lane(action: str) -> str:
    a = str(action or "").upper()
    return "buy" if a.startswith("BUY") else "sell" if a.startswith("SELL") else "other"


def _todays_trades(instrument: str, mode: str, lane: str) -> list[dict[str, datetime | None]]:
    """(opened, closed) IST datetimes for real fills on this index + mode + lane,
    where either the entry or the exit landed today."""
    from index_ai.learning import connect, is_live_trade

    want_live = str(mode or "PAPER").upper() == "LIVE"
    day = today_ist_date()
    out: list[dict[str, datetime | None]] = []
    with connect() as db:
        rows = db.execute(
            "SELECT created_at, action, option_json, pnl, mode, status FROM trades "
            "WHERE instrument = ? ORDER BY created_at DESC LIMIT 80",
            (instrument,),
        ).fetchall()
    for r in rows:
        if str(r["status"] or "").upper() in _UNFILLED:
            continue
        if is_live_trade({"mode": r["mode"], "status": r["status"]}) != want_live:
            continue
        if _lane(r["action"]) != lane:
            continue
        opened = parse_ist_datetime(str(r["created_at"] or ""))
        closed = None
        if r["pnl"] is not None:
            try:
                closed = parse_ist_datetime(
                    str(json.loads(r["option_json"] or "{}").get("closed_at") or "")
                )
            except Exception:
                closed = None
        today_open = opened is not None and opened.date().isoformat() == day
        today_close = closed is not None and closed.date().isoformat() == day
        if today_open or today_close:
            out.append({"opened": opened, "closed": closed})
    return out


def _regime_blocks(instrument: str, regime: dict[str, Any] | None) -> tuple[bool, str]:
    """A directional credit spread needs a real trend: CPR broken, not wide."""
    r = regime or {}
    wc = str(r.get("width_class") or "").upper()
    pp = str(r.get("price_position") or "").lower()
    if wc == "WIDE" or pp == "inside_cpr":
        return True, (
            f"no-trend regime for {instrument} — CPR {wc.lower() or 'range'}"
            + (", price inside the range" if pp == "inside_cpr" else "")
        )
    return False, ""


def check(
    instrument: str, mode: str, regime: dict[str, Any] | None = None, *, lane: str = "sell"
) -> tuple[bool, str]:
    """(blocked, reason). Call before opening a new position for this index + lane."""
    inst = str(instrument or "").strip().upper()
    now = now_ist()
    day = today_ist_date()
    trades = _todays_trades(inst, mode, lane)

    cap = daily_cap(inst)
    entries_today = sum(
        1 for t in trades if t["opened"] is not None and t["opened"].date().isoformat() == day
    )
    if entries_today >= cap:
        return True, f"daily trade cap for {inst} {lane} hit ({entries_today}/{cap})"

    closes = sorted((t["closed"] for t in trades if t["closed"]), reverse=True)
    if closes:
        since_last = (now - closes[0]).total_seconds() / 60.0
        if since_last < REENTRY_COOLDOWN_MIN:
            return True, (
                f"re-entry cooldown for {inst} — {since_last:.0f}m since last close, "
                f"need {REENTRY_COOLDOWN_MIN}m"
            )
        in_last_hour = [c for c in closes if (now - c).total_seconds() <= 3600]
        if len(in_last_hour) >= CHOP_CLOSES_PER_HOUR and since_last < CHOP_LOCKOUT_MIN:
            return True, (
                f"chop lockout for {inst} — {len(in_last_hour)} round-trips in the last "
                f"hour, sitting out {CHOP_LOCKOUT_MIN}m"
            )

    return _regime_blocks(inst, regime)


if __name__ == "__main__":  # self-check (pure logic — no journal read)
    assert daily_cap("BANKNIFTY") == 4 and daily_cap("NIFTY") == 6
    os.environ["DAILY_TRADE_CAP_BANKNIFTY"] = "2"
    assert daily_cap("BANKNIFTY") == 2
    del os.environ["DAILY_TRADE_CAP_BANKNIFTY"]

    assert _regime_blocks("NIFTY", {"width_class": "WIDE"})[0]
    assert "inside the range" in _regime_blocks("NIFTY", {"price_position": "inside_cpr"})[1]
    assert not _regime_blocks("NIFTY", {"width_class": "NORMAL", "price_position": "above_cpr"})[0]
    print("entry_guard.py self-check ok — cap + regime gates")
