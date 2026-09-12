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

from index_ai.env import env_int as _int
from index_ai.market_clock import now_ist, parse_ist_datetime, today_ist_date

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


def _enforce_regime_gate() -> bool:
    raw = os.getenv("ENFORCE_REGIME_GATE")
    return True if raw is None else raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _require_viable() -> bool:
    # default OFF: the sell-lane gross edge was re-measured negative 2026-09-09,
    # but the signal was just retimed (5m/15m) — don't auto-pause the lane on a
    # stale verdict. Flip OPTIONS_REQUIRE_VIABLE=true to arm it.
    raw = os.getenv("OPTIONS_REQUIRE_VIABLE")
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _viable_sell_blocks(instrument: str) -> tuple[bool, str]:
    """Block the credit-sell lane on an index whose measured gross edge cannot
    cover its measured round-trip cost floor (options_cpr/viability.py). Sell lane
    only — the buy lane keeps its own liquidity + confidence gates. Never raises;
    an unmeasured or errored verdict does not block."""
    if not _require_viable():
        return False, ""
    try:
        from index_ai.strategies.options_cpr.config import config_for, with_overrides
        from index_ai.strategies.options_cpr.viability import NOT_VIABLE, viability

        # the sell lane is 2-leg hedged directional only (no naked)
        cfg = with_overrides(config_for(instrument), sell_naked=False)
        v = viability(instrument, "sell", cfg=cfg)
        if v.verdict == NOT_VIABLE:
            return True, f"not viable on {instrument} — {v.reason}"
    except Exception:
        return False, ""
    return False, ""


def regime_blocks_lane(read: dict[str, Any] | None, lane: str) -> tuple[bool, str]:
    """Brain market-regime veto (index_ai/brain/regime.py): QUIET stands both
    lanes down, RANGE blocks buying, HIGH_VOL allows selling but on a tighter
    stop (see TIGHTEN_SELL_STOP_KEY), TREND allows all. Uses the read's own
    allow_* flags so it tracks the classifier."""
    if not read or not _enforce_regime_gate():
        return False, ""
    key = f"allow_{str(lane).lower()}"
    if key in read and not read.get(key):
        return True, f"regime {read.get('regime')} — {lane} lane stood down ({read.get('reason')})"
    return False, ""


def _regime_blocks(
    instrument: str, regime: dict[str, Any] | None, intraday_trend: str | None = None
) -> tuple[bool, str]:
    """CPR is a guide, not a gate. The only CPR-shaped no-trade left is a wide
    CPR *and* a genuinely rangebound intraday tape (no HH/HL or LH/LL swing
    structure). Price sitting inside the central range is no longer a block —
    a confirmed intraday trend through the CPR is exactly the setup we want."""
    wc = str((regime or {}).get("width_class") or "").upper()
    if wc == "WIDE" and str(intraday_trend or "").upper() == "RANGE":
        return True, (f"no-trend regime for {instrument} — wide CPR and rangebound intraday tape")
    return False, ""


def check(
    instrument: str,
    mode: str,
    regime: dict[str, Any] | None = None,
    *,
    lane: str = "sell",
    regime_read: dict[str, Any] | None = None,
    intraday_trend: str | None = None,
) -> tuple[bool, str]:
    """(blocked, reason). Call before opening a new position for this index + lane."""
    inst = str(instrument or "").strip().upper()
    now = now_ist()
    day = today_ist_date()

    blocked, why = regime_blocks_lane(regime_read, lane)
    if blocked:
        return True, why

    if lane == "sell":
        blocked, why = _viable_sell_blocks(inst)
        if blocked:
            return True, why

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

    return _regime_blocks(inst, regime, intraday_trend)


if __name__ == "__main__":  # self-check (pure logic — no journal read)
    assert daily_cap("BANKNIFTY") == 4 and daily_cap("NIFTY") == 6
    os.environ["DAILY_TRADE_CAP_BANKNIFTY"] = "2"
    assert daily_cap("BANKNIFTY") == 2
    del os.environ["DAILY_TRADE_CAP_BANKNIFTY"]

    assert _regime_blocks("NIFTY", {"width_class": "WIDE"}, "RANGE")[0]
    assert not _regime_blocks("NIFTY", {"width_class": "WIDE"}, "DOWN")[
        0
    ]  # trending through it — fine
    assert not _regime_blocks("NIFTY", {"width_class": "WIDE"})[0]  # unknown trend — CPR is a guide
    assert not _regime_blocks("NIFTY", {"price_position": "inside_cpr"})[0]  # no longer a block

    assert regime_blocks_lane({"regime": "QUIET", "allow_buy": False, "allow_sell": False}, "buy")[
        0
    ]
    assert regime_blocks_lane({"regime": "RANGE", "allow_buy": False, "allow_sell": True}, "buy")[0]
    assert not regime_blocks_lane(
        {"regime": "RANGE", "allow_buy": False, "allow_sell": True}, "sell"
    )[0]
    assert regime_blocks_lane(
        {"regime": "HIGH_VOL", "allow_buy": True, "allow_sell": False}, "sell"
    )[0]
    assert not regime_blocks_lane(
        {"regime": "HIGH_VOL", "allow_buy": True, "allow_sell": False}, "buy"
    )[0]
    assert not regime_blocks_lane(None, "buy")[0]

    # viability sell gate is opt-in (default off); when armed a NOT_VIABLE index
    # is blocked, an UNMEASURED one is not
    os.environ["SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY"] = "4.06"
    assert not _viable_sell_blocks("BANKNIFTY")[0]  # gate default-off
    os.environ["OPTIONS_REQUIRE_VIABLE"] = "true"
    assert _viable_sell_blocks("BANKNIFTY")[0]
    assert not _viable_sell_blocks("SENSEX")[0]  # UNMEASURED — never blocks
    del os.environ["OPTIONS_REQUIRE_VIABLE"]
    del os.environ["SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY"]

    os.environ["ENFORCE_REGIME_GATE"] = "false"
    assert not regime_blocks_lane(
        {"regime": "QUIET", "allow_buy": False, "allow_sell": False}, "buy"
    )[0]
    del os.environ["ENFORCE_REGIME_GATE"]
    print("entry_guard.py self-check ok — cap + regime gates")
