"""Stop-distance re-check: each market's stop distance against its own recent trades.

Run once a day and on demand ("Re-check now"). For each market and lane it
looks only at the trades that were really taken under today's stop distance
and reports how many there are, how many days they cover, how often they won
after real broker charges, how often the stop itself ended the trade, and the
net rupees.

It only reports, and may later suggest. It never changes a stop, an env
setting or a strategy parameter -- a person approves any change (D-02). Below
the project's usual bar (trades AND trading days, the same ladder as
``strategy_learning``) it says "not enough data yet" and still shows the real
numbers (D-03). Its only write is its own ``learned_settings`` row.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

from index_ai import market_log
from index_ai.data_epoch import data_epoch
from index_ai.market_clock import now_ist_iso
from index_ai.strategy_learning import (
    FREEZE_LOOKBACK,
    OBSERVE_MAX,
    READY_MIN_DAYS,
    _frozen,
    _state,
)
from index_ai.strategy_performance import _after_epoch, _india_charges

SETTINGS_KEY = "exit_recheck_state"
TRIGGERS = ("daily", "button")

# One pass at a time: the daily run and the button (or a double click) must not
# interleave their recompute-and-store.
_RECHECK_LOCK = threading.Lock()

# Replay rules (03-04). All judgement calls; do not loosen them to make real numbers look better.
# Four of every five real exits must be reproduced, else the rupee comparison measures the
# replay's own error as much as the stop.
MATCH_MIN = 0.8
# The live stop is checked on every tick (worst case every 20 s), so a correct replay lands well
# inside two minutes; tick-delivery lag days will honestly show up as mismatches.
MATCH_TOLERANCE_S = 120
# The option chain is recorded about once a minute; a quote older than this is not a price.
QUOTE_MAX_AGE_S = 300
# Alternative stop distances tried: today's x these, rounded to the nearest DISTANCE_STEP.
CANDIDATE_FACTORS = (0.75, 1.25, 1.5)
DISTANCE_STEP = 5.0  # index points

# First match wins, in this order. Matched against the lower-cased exit note.
_INDIA_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("manual", re.compile(r"^manual close")),
    (
        "time_exit",
        re.compile(r"square-off|square off|end-of-session|flat at scanner|prior session"),
    ),
    ("trail_stop", re.compile(r"^index trail:|^trailing stop armed|^initial stop \(")),
    ("legacy_premium", re.compile(r"^hard stop: premium|^trailing exit: premium")),
    (
        "other_rule",
        re.compile(
            r"max loss|credit stop loss|below short put|above short call|profit target"
            r"|^profit trail:|^iron condor:"
        ),
    ),
    ("signal_exit", re.compile(r"regime|strategy signal|supertrend|directional signal|^auto:")),
)


def classify_exit(kind: str, reason: str | None) -> str:
    """Name the class of an exit reason. An unseen reason is "other", never dropped.

    ``kind`` is "india" for now; crypto and commodities have their own wording
    and are added with their segments.
    """
    text = str(reason or "").strip().lower()
    if kind == "india":
        for name, pattern in _INDIA_RULES:
            if pattern.search(text):
                return name
    return "other"


def _india_rows() -> dict[str, list[dict[str, Any]]]:
    """segment -> trades taken under today's stop distance, newest first."""
    from index_ai import day_review
    from index_ai.instruments import get_instrument
    from index_ai.learning import recent_trades
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS
    from index_ai.strategies.strategy_router import trade_lane

    epoch = data_epoch()
    notes = day_review._exit_notes()
    out: dict[str, list[dict[str, Any]]] = {
        _segment_id(inst, lane): [] for inst in SELL_TRAIL_POINTS for lane in ("buy", "sell")
    }
    for t in recent_trades(limit=1_000_000):
        if t.get("pnl") is None or not _after_epoch(t.get("created_at"), epoch):
            continue
        lane = trade_lane(str(t.get("action") or ""))
        option = t.get("option") or {}
        inst = str(t.get("instrument") or option.get("instrument") or "").strip().upper()
        if lane not in ("buy", "sell") or inst not in SELL_TRAIL_POINTS:
            continue
        meta = option.get("trail_meta") or {}
        # keep only trades whose OWN recorded distance is today's distance
        if lane == "sell":
            if float(meta.get("it_points") or 0) != SELL_TRAIL_POINTS[inst]:
                continue
        else:
            if (
                float(meta.get("trail_distance_points") or 0)
                != get_instrument(inst).trail_distance_points
                or float(meta.get("trail_activation_points") or 0) != 0.0
            ):
                continue
        gross = float(t["pnl"])
        cs = _india_charges(t)
        out[_segment_id(inst, lane)].append(
            {
                "id": t.get("id"),
                "day": str(t.get("created_at") or "")[:10],
                "net": gross - cs[0] - cs[1] if cs else gross,
                "cls": classify_exit("india", notes.get(str(t.get("id")))),
                "_trade": t,  # raw trade for the replay only; never stored (see _segment_row)
            }
        )
    return out


def _segment_id(inst: str, lane: str) -> str:
    return f"india_{inst}_{lane}"


def _distance(inst: str, lane: str) -> float:
    """Today's stop distance, in index points, for an India index and lane."""
    from index_ai.instruments import get_instrument
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

    return SELL_TRAIL_POINTS[inst] if lane == "sell" else get_instrument(inst).trail_distance_points


def _segment_row(
    segment: str,
    venue: str,
    lane: str,
    instrument: str,
    label: str,
    distance: float,
    distance_label: str,
    currency: str,
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """The one row shape every venue shares. ``trades`` is newest first."""
    n = len(trades)
    days = len({t["day"] for t in trades if t["day"]})
    nets = [t["net"] for t in trades]
    window = trades[:OBSERVE_MAX]
    wn = len(window)
    wins = sum(1 for t in window if t["net"] > 0)
    hits = sum(1 for t in window if t["cls"] == "trail_stop")
    row: dict[str, Any] = {
        "segment": segment,
        "venue": venue,
        "lane": lane,
        "instrument": instrument,
        "label": label,
        "distance": distance,
        "distance_label": distance_label,
        "currency": currency,
        "trades": n,
        "trading_days": days,
        "state": _state(n, days),
        "frozen": _frozen(nets[:FREEZE_LOOKBACK]),
        "window_n": wn,
        "wins": wins,
        "trail_hits": hits,
        "win_rate": round(wins / wn, 3) if wn else None,
        "trail_hit_rate": round(hits / wn, 3) if wn else None,
        "net": round(sum(nets), 2),
        "exit_mix": dict(Counter(t["cls"] for t in window)),
        "suggestion": None,
        "drift": False,
        "baseline": None,
    }
    row["verdict"], row["message"] = _verdict(row, trades)
    return row


def _verdict(row: dict[str, Any], trades: list[dict[str, Any]]) -> tuple[str, str]:
    """The segment's verdict, in a fixed order that must not be rearranged: not ready on the
    ladder -> not_enough_data; frozen (net-positive recent trades) -> working, BEFORE any replay is
    opened; only then, for India, the replay gate. ``trades`` is the segment's newest-first rows."""
    if row["state"] != "ready":
        return (
            "not_enough_data",
            f"Not enough trades yet to judge this stop — {row['trades']} of {OBSERVE_MAX} "
            f"trades and {row['trading_days']} of {READY_MIN_DAYS} trading days so far "
            f"under today's {row['distance_label']}.",
        )
    if row["frozen"]:  # frozen first: a net-positive stop is never second-guessed
        return "working", "Making money over its recent trades — no change suggested."
    if row["venue"] != "india":  # crypto and commodities have no recorded price path
        return (
            "no_replay_data",
            "Enough trades to judge, but a different stop can't be tested on these trades yet — "
            "the numbers above are still watched for changes.",
        )
    return _india_replay_gate(row, trades)


def _india_replay_gate(row: dict[str, Any], trades: list[dict[str, Any]]) -> tuple[str, str]:
    """Replay quality, then net rupees after real charges. A suggestion is a sentence plus numbers
    stored in ``row["suggestion"]``; nothing here applies it or touches any stop setting (D-02)."""
    dist = row["distance"]
    try:
        with _market_log_ro() as db:
            rep = replay_segment(row["lane"], row["instrument"], trades, dist, db)
    except Exception:
        return "replay_unreliable", "Could not read the recorded prices, so no new stop is suggested yet."
    seen, matched, rate = rep["trades_with_ticks"], rep["matched"], rep["match_rate"]
    if rate is None:
        return (
            "replay_unreliable",
            "None of the past trades could be replayed from the recorded prices, so no new stop "
            "is suggested yet.",
        )
    if rate < MATCH_MIN:
        return (
            "replay_unreliable",
            f"The replay of past trades only reproduced {matched} of {seen} real exits, so no new "
            "stop is suggested yet.",
        )
    n = rep["compared"]
    if n < OBSERVE_MAX:
        return (
            "replay_unreliable",
            f"Only {n} of the last {OBSERVE_MAX} trades could be priced from the recorded option "
            "prices, so no new stop is suggested yet.",
        )
    now = rep["by_distance"][dist]
    others = {d: v for d, v in rep["by_distance"].items() if d != dist}
    best = max(others, key=lambda d: others[d]["net"]) if others else None
    if best is None or others[best]["net"] <= now["net"]:
        tried = ", ".join(f"{d:g}" for d in sorted(others))
        return (
            "no_better_distance",
            f"None of the other stops tried ({tried} points) would have made more over the last "
            f"{n} trades — keep the {dist:g}-point stop.",
        )
    alt = others[best]
    extra = alt["net"] - now["net"]
    rough = alt["extended"] > 0
    row["suggestion"] = {
        "distance": best,
        "current": dist,
        "extra_net": round(extra, 2),
        "trades": n,
        "win_rate_now": round(now["wins"] / n, 3),
        "win_rate_alt": round(alt["wins"] / n, 3),
        "match_rate": round(rate, 3),
        "rough": rough,
    }
    text = (
        f"{row['label']} {dist:g}-point stop looks too {'tight' if best > dist else 'loose'} — "
        f"{best:g} points would have kept ₹{round(extra):,} more over the last {n} trades "
        f"(win rate {round(100 * now['wins'] / n)}% -> {round(100 * alt['wins'] / n)}%)."
    )
    if rough:
        text += (
            " The figure is rough: some trades had to be held longer than they really were, and "
            "other exits in that extra time cannot be replayed."
        )
    return "suggestion", text + " Nothing was changed — this is only a suggestion for you to approve."


def compute_segments() -> dict[str, Any]:
    """Every segment's current numbers. Pure: reads journals, writes nothing."""
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

    segments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        rows = _india_rows()
        for inst in SELL_TRAIL_POINTS:
            for lane in ("buy", "sell"):
                dist = _distance(inst, lane)
                segments.append(
                    _segment_row(
                        _segment_id(inst, lane),
                        "india",
                        lane,
                        inst,
                        f"{inst} {lane}",
                        dist,
                        f"{dist:g}-point stop",
                        "INR",
                        rows[_segment_id(inst, lane)],
                    )
                )
    except Exception as exc:
        errors.append({"venue": "india", "error": str(exc)[:200]})
    return {"segments": segments, "errors": errors}


# --- India replay (03-04): when would a different stop have closed a past trade? ----------------
#
# Read-only and honest: prices come only from the recorded index ticks and the recorded option
# chain (bid/ask). No candles, no Black-Scholes proxy, no interpolation -- a leg with no quote is
# reported as "could not price". The result is numbers only; nothing here applies a distance.

_DIRECTION = {"BUY_CALL": 1, "SELL_BULL_PUT_SPREAD": 1, "BUY_PUT": -1, "SELL_BEAR_CALL_SPREAD": -1}


@contextmanager
def _market_log_ro() -> Iterator[sqlite3.Connection]:
    """The market log opened read-only. The market log module's own connect helper is NOT used: it
    switches the file to WAL, runs a migration and commits -- all writes to a live database the
    scanner is filling."""
    uri = market_log.DB_PATH.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _candidates(distance: float) -> list[float]:
    """Today's distance x each factor, to the nearest DISTANCE_STEP (halves round up)."""
    out = {math.floor(distance * f / DISTANCE_STEP + 0.5) * DISTANCE_STEP for f in CANDIDATE_FACTORS}
    return sorted(d for d in out if d > 0 and d != distance)


def _replay_exit(trade: dict[str, Any], distance: float, path: Any, real_exit_ts: Any, real_cls: str):
    """(exit time, how, extended) for a 1:1 stop at ``distance``, or None if it can't be replayed.

    Same anchor rule as ``strategy_lab._trail_hit`` (which only returns a bool, so it cannot be
    called here): direction +1 -> anchor is the best (highest) price since entry and the stop is
    touched when price <= anchor - distance; direction -1 mirrors it. The anchor starts at the
    trade's own entry index price. ``how``: "trail" (stop crossed), "real_exit" (the trade really
    ended another way first), "square_off" (never crossed). Nothing later than the 15:10 square-off.
    """
    import numpy as np
    import pandas as pd

    from index_ai import strategy_lab

    direction = _DIRECTION.get(str(trade.get("action") or ""))
    entry_px = ((trade.get("option") or {}).get("trail_meta") or {}).get("entry_index_price")
    if direction is None or not entry_px or path.empty:
        return None
    entry_px = float(entry_px)
    entry = pd.Timestamp(trade["created_at"])
    cutoff = pd.Timestamp(f"{str(trade['created_at'])[:10]}T{strategy_lab.SQUARE_OFF}:00+05:30")
    lo = path.index.searchsorted(entry, side="right")  # ticks after entry, up to the square-off
    hi = path.index.searchsorted(cutoff, side="right")
    seg = path.iloc[lo:hi]
    px = seg.to_numpy()
    if direction > 0:
        hits = px <= np.maximum.accumulate(np.maximum(px, entry_px)) - distance
    else:
        hits = px >= np.minimum.accumulate(np.minimum(px, entry_px)) + distance
    crossing = seg.index[int(hits.argmax())] if hits.any() else None
    if real_cls != "trail_stop" and (crossing is None or real_exit_ts <= crossing):
        return real_exit_ts, "real_exit", False  # it really ended another way first
    if crossing is not None:
        return crossing, "trail", bool(crossing > real_exit_ts)
    return cutoff, "square_off", bool(cutoff > real_exit_ts)


def _quotes_at(db: Any, instrument: str, session: str, ts: Any) -> dict[str, dict[str, Any]] | None:
    """{security_id: {bid, ask}} from the newest recorded chain snapshot at or before ``ts`` --
    None if there is none, or it is older than QUOTE_MAX_AGE_S."""
    import pandas as pd

    stamp = ts.tz_convert("Asia/Kolkata").strftime("%Y-%m-%dT%H:%M:%S+05:30")
    snap = db.execute(
        "SELECT MAX(ts) FROM chain WHERE session=? AND instrument=? AND ts<=?",
        (session, instrument, stamp),
    ).fetchone()[0]
    if not snap or (ts - pd.Timestamp(snap)).total_seconds() > QUOTE_MAX_AGE_S:
        return None
    rows = db.execute(
        "SELECT security_id, bid, ask FROM chain"
        " WHERE session=? AND instrument=? AND ts=? AND security_id IS NOT NULL",
        (session, instrument, snap),
    ).fetchall()
    return {str(r[0]): {"bid": r[1], "ask": r[2]} for r in rows}


def _net_at(trade: dict[str, Any], quotes: dict[str, dict[str, Any]] | None) -> float | None:
    """Net rupees of closing the trade into ``quotes``: a bought leg sells at the bid, a sold leg
    buys back at the ask, entry at the journal's own leg prices, real Dhan charges on both sides.
    None if any leg has no recorded quote -- never estimated."""
    from index_ai import strategy_lab
    from index_ai.charges import leg_charge_rupees

    if not quotes:
        return None
    opt = trade.get("option") or {}
    qty = int(opt.get("quantity") or 0)
    inst = str(trade.get("instrument") or opt.get("instrument") or "").upper()
    exch = "BSE" if inst == "SENSEX" else "NSE"
    net = 0.0
    for leg in opt.get("legs") or [opt]:
        side = str(leg.get("transaction_type") or "").upper()
        entry = float(leg.get("ltp") or 0)
        if side not in ("BUY", "SELL") or entry <= 0 or qty <= 0:
            return None
        closing = strategy_lab._flip(side)
        px = strategy_lab._fill(quotes.get(str(leg.get("security_id"))), closing)
        if px is None:
            return None
        net += ((px - entry) if side == "BUY" else (entry - px)) * qty
        net -= leg_charge_rupees(entry, qty, side, exchange=exch)
        net -= leg_charge_rupees(px, qty, closing, exchange=exch)
    return net


def replay_segment(
    lane: str,
    instrument: str,
    records: list[dict[str, Any]],
    distance: float,
    db: Any,
    _paths: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    """Replay a segment's newest OBSERVE_MAX trades under today's distance and each candidate.

    ``matched`` counts trades whose replay at TODAY's distance agrees with what really happened:
    a real stop exit matches when the replayed stop fires within MATCH_TOLERANCE_S of it; any
    other real exit matches unless the replayed stop would have fired more than MATCH_TOLERANCE_S
    earlier. ``compared`` counts trades priced at today's distance AND every candidate; the
    rupee sums in ``by_distance`` are over exactly those trades (unrounded).
    """
    import pandas as pd

    from index_ai import strategy_lab

    paths = {} if _paths is None else _paths
    dists = [distance, *_candidates(distance)]
    out: dict[str, Any] = {
        "lane": lane,
        "instrument": instrument,
        "distance": distance,
        "trades_with_ticks": 0,
        "matched": 0,
        "match_rate": None,
        "could_not_replay": 0,
        "could_not_price": 0,
        "compared": 0,
        "by_distance": {d: {"net": 0.0, "wins": 0, "extended": 0} for d in dists},
    }
    quotes: dict[Any, Any] = {}
    for rec in records[:OBSERVE_MAX]:
        trade, cls = rec["_trade"], rec["cls"]
        closed = (trade.get("option") or {}).get("closed_at")
        session = str(trade.get("created_at") or "")[:10]
        if not closed or not session:
            out["could_not_replay"] += 1
            continue
        if (session, instrument) not in paths:
            paths[(session, instrument)] = strategy_lab._index_path(instrument, session, db=db)
        real_exit = pd.Timestamp(closed)
        exits = {d: _replay_exit(trade, d, paths[(session, instrument)], real_exit, cls) for d in dists}
        if any(e is None for e in exits.values()):  # no entry price / no ticks / unknown action
            out["could_not_replay"] += 1
            continue
        out["trades_with_ticks"] += 1
        cur_ts, cur_how, _ = exits[distance]
        if cls == "trail_stop":
            ok = cur_how == "trail" and abs((cur_ts - real_exit).total_seconds()) <= MATCH_TOLERANCE_S
        else:
            ok = not (cur_how == "trail" and (real_exit - cur_ts).total_seconds() > MATCH_TOLERANCE_S)
        out["matched"] += int(ok)
        nets = {}
        for d, (ts, _how, _ext) in exits.items():
            if (session, ts) not in quotes:
                quotes[(session, ts)] = _quotes_at(db, instrument, session, ts)
            nets[d] = _net_at(trade, quotes[(session, ts)])
        if any(n is None for n in nets.values()):
            out["could_not_price"] += 1
            continue
        out["compared"] += 1
        for d, n in nets.items():
            b = out["by_distance"][d]
            b["net"] += n
            b["wins"] += int(n > 0)
            b["extended"] += int(exits[d][2])
    if out["trades_with_ticks"]:
        out["match_rate"] = out["matched"] / out["trades_with_ticks"]
    return out


def replay_diagnostic() -> list[dict[str, Any]]:
    """A manual look: replay all six India segments regardless of the ladder. Informational only
    -- never called by the daily run, and a result here is never a suggestion."""
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

    rows = _india_rows()
    out: list[dict[str, Any]] = []
    paths: dict[Any, Any] = {}
    with _market_log_ro() as db:
        for inst in SELL_TRAIL_POINTS:
            for lane in ("buy", "sell"):
                seg = _segment_id(inst, lane)
                try:
                    res = replay_segment(
                        lane, inst, rows[seg], _distance(inst, lane), db, _paths=paths
                    )
                    out.append({"segment": seg, **res})
                except Exception as exc:
                    out.append({"segment": seg, "error": str(exc)[:200]})
    return out


def _load_state() -> dict[str, Any] | None:
    from index_ai.learning import connect

    with connect() as db:
        row = db.execute(
            "SELECT value_json FROM learned_settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["value_json"])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _store_state(state: dict[str, Any]) -> None:
    """Write the whole state (absolute values, never a delta) as one row."""
    from index_ai.learning import connect, now_utc

    with connect() as db:
        db.execute(
            """
            INSERT INTO learned_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (SETTINGS_KEY, json.dumps(state, default=str), now_utc()),
        )


def last_result() -> dict[str, Any]:
    return _load_state() or {"ran_at": None, "trigger": None, "segments": [], "errors": []}


def run_recheck(trigger: str = "button") -> dict[str, Any]:
    """Recompute and store. A caller that finds a pass already running waits for
    it and returns what it stored, instead of computing the same thing again."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    if not _RECHECK_LOCK.acquire(blocking=False):
        with _RECHECK_LOCK:
            pass
        return last_result()
    try:
        state = compute_segments()
        state["ran_at"] = now_ist_iso()
        state["trigger"] = trigger
        _store_state(state)
        return state
    finally:
        _RECHECK_LOCK.release()
