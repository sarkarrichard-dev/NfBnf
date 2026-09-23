"""
Option-chain signals, read from the real snapshots in ``market_log.chain`` —
and graded against what the index actually did next.

Nothing here places a trade. Each signal casts a vote (+1 up, -1 down, 0 no
view) from how today's option chain has changed since the session's first
snapshot:

  walls      the biggest put-OI strike (support) and call-OI strike
             (resistance) both moved up -> +1, both down -> -1.
  writing    near spot, were more puts or more calls written today? Put
             writers adding OI are betting it won't fall -> +1; call
             writers -> -1.
  pcr        put/call OI ratio across the window rose / fell by > 0.10.
  max_pain   expiry day, after 12:00 only: spot tends to be pulled toward
             max pain, so vote in its direction when it's > 0.2% away.

``bias`` is BULLISH / BEARISH when the votes add up to +-2 or more.
``grade()`` replays each recorded session using only the snapshots available
at each moment (no peeking ahead) and checks the index 30 minutes later.
That is the whole test: real recorded prices, no Black-Scholes, no backtest
candles. A signal that can't beat a coin flip here never reaches a trade.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from index_ai import market_log
from index_ai.market_context.oi_flow import max_pain

NEAR_STRIKES = 5  # "near spot" for the writing vote: ±5 strikes
PCR_MOVE = 0.10
MAX_PAIN_GAP_PCT = 0.2
FORWARD_MINUTES = 30

Snapshot = list[dict[str, Any]]  # the chain rows of one timestamp


def load_session(instrument: str, session: str) -> list[tuple[str, Snapshot]]:
    """``[(ts, rows), ...]`` in time order for one index and day."""
    with market_log.connect() as db:
        rows = db.execute(
            "SELECT ts, expiry, spot, strike, opt_type, oi, ltp, bid, ask, iv FROM chain "
            "WHERE instrument=? AND session=? ORDER BY ts, strike",
            (instrument.upper(), session),
        ).fetchall()
    snaps: dict[str, Snapshot] = defaultdict(list)
    for r in rows:
        snaps[r["ts"]].append(dict(r))
    return list(snaps.items())


def _oi_by_side(snap: Snapshot) -> dict[str, dict[float, float]]:
    out: dict[str, dict[float, float]] = {"CE": {}, "PE": {}}
    for r in snap:
        out[r["opt_type"]][r["strike"]] = r["oi"] or 0.0
    return out


def _wall(side: dict[float, float]) -> float | None:
    return max(side, key=side.get) if side and max(side.values()) > 0 else None


def _pcr(oi: dict[str, dict[float, float]]) -> float | None:
    ce = sum(oi["CE"].values())
    return sum(oi["PE"].values()) / ce if ce > 0 else None


def read(first: Snapshot, now: Snapshot, session: str) -> dict[str, Any]:
    """Votes + bias from the session's first snapshot and the current one."""
    spot = float(now[0]["spot"] or 0)
    expiry = now[0]["expiry"]
    a, b = _oi_by_side(first), _oi_by_side(now)
    votes: dict[str, int] = {}
    why: dict[str, str] = {}

    sup0, res0, sup1, res1 = _wall(a["PE"]), _wall(a["CE"]), _wall(b["PE"]), _wall(b["CE"])
    votes["walls"] = 0
    if None not in (sup0, res0, sup1, res1):
        if sup1 > sup0 and res1 > res0:
            votes["walls"] = 1
        elif sup1 < sup0 and res1 < res0:
            votes["walls"] = -1
        why["walls"] = f"support {sup0:g}->{sup1:g}, resistance {res0:g}->{res1:g}"

    near = sorted(b["CE"].keys() | b["PE"].keys(), key=lambda k: abs(k - spot))[
        : 2 * NEAR_STRIKES + 1
    ]
    d_pe = sum(b["PE"].get(k, 0) - a["PE"].get(k, 0) for k in near)
    d_ce = sum(b["CE"].get(k, 0) - a["CE"].get(k, 0) for k in near)
    votes["writing"] = 0
    if abs(d_pe) + abs(d_ce) > 0:
        tilt = (d_pe - d_ce) / (abs(d_pe) + abs(d_ce))
        votes["writing"] = 1 if tilt > 0.25 else -1 if tilt < -0.25 else 0
        why["writing"] = f"put OI {d_pe:+,.0f}, call OI {d_ce:+,.0f} near spot"

    p0, p1 = _pcr(a), _pcr(b)
    votes["pcr"] = 0
    if p0 is not None and p1 is not None:
        votes["pcr"] = 1 if p1 - p0 > PCR_MOVE else -1 if p0 - p1 > PCR_MOVE else 0
        why["pcr"] = f"PCR {p0:.2f}->{p1:.2f}"

    votes["max_pain"] = 0
    ts = str(now[0].get("ts") or "")
    if expiry == session and ts[11:16] >= "12:00" and spot:
        mp = max_pain(
            {
                k: {"ce": {"oi": b["CE"].get(k, 0)}, "pe": {"oi": b["PE"].get(k, 0)}}
                for k in b["CE"].keys() | b["PE"].keys()
            }
        )
        if mp:
            gap = (mp - spot) / spot * 100
            if abs(gap) > MAX_PAIN_GAP_PCT:
                votes["max_pain"] = 1 if gap > 0 else -1
            why["max_pain"] = f"max pain {mp:g} vs spot {spot:g} ({gap:+.2f}%)"

    score = sum(votes.values())
    bias = "BULLISH" if score >= 2 else "BEARISH" if score <= -2 else "NEUTRAL"
    return {"bias": bias, "score": score, "votes": votes, "why": why, "spot": spot}


def latest(instrument: str, session: str) -> dict[str, Any] | None:
    snaps = load_session(instrument, session)
    if len(snaps) < 2:
        return None
    out = read(snaps[0][1], snaps[-1][1], session)
    out["at"] = snaps[-1][0]
    return out


def grade(instrument: str, sessions: list[str]) -> dict[str, Any]:
    """Replay each session snapshot by snapshot; score every vote and the
    combined bias against the index ``FORWARD_MINUTES`` later.

    Consecutive readings overlap (a 90s cadence against a 30-minute look
    ahead), so ``calls`` overstates the independent sample — judge on
    ``sessions_with_calls`` too, and don't trust anything under ~15 days.
    """
    names = ("bias", "walls", "writing", "pcr", "max_pain")
    tally = {n: {"calls": 0, "right": 0, "points": 0.0, "days": set()} for n in names}
    for session in sessions:
        snaps = load_session(instrument, session)
        times = [datetime.fromisoformat(ts) for ts, _ in snaps]
        j = 0
        for i in range(1, len(snaps)):
            target = times[i] + timedelta(minutes=FORWARD_MINUTES)
            j = max(j, i)
            while j < len(snaps) and times[j] < target:
                j += 1
            if j == len(snaps):
                break
            sig = read(snaps[0][1], snaps[i][1], session)
            move = float(snaps[j][1][0]["spot"] or 0) - sig["spot"]
            calls = {"bias": {"BULLISH": 1, "BEARISH": -1}.get(sig["bias"], 0), **sig["votes"]}
            for n, v in calls.items():
                if v:
                    t = tally[n]
                    t["calls"] += 1
                    t["right"] += move * v > 0
                    t["points"] += move * v
                    t["days"].add(session)
    return {
        n: {
            "calls": t["calls"],
            "sessions_with_calls": len(t["days"]),
            "hit_rate": round(t["right"] / t["calls"], 3) if t["calls"] else None,
            "avg_points": round(t["points"] / t["calls"], 2) if t["calls"] else None,
        }
        for n, t in tally.items()
    }
