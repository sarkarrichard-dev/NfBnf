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
import re
import threading
from collections import Counter
from typing import Any

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
            }
        )
    return out


def _segment_id(inst: str, lane: str) -> str:
    return f"india_{inst}_{lane}"


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
    row["verdict"], row["message"] = _verdict(row)
    return row


def _verdict(row: dict[str, Any]) -> tuple[str, str]:
    if row["state"] != "ready":
        return (
            "not_enough_data",
            f"Not enough trades yet to judge this stop — {row['trades']} of {OBSERVE_MAX} "
            f"trades and {row['trading_days']} of {READY_MIN_DAYS} trading days so far "
            f"under today's {row['distance_label']}.",
        )
    if row["frozen"]:  # frozen first: a net-positive stop is never second-guessed
        return "working", "Making money over its recent trades — no change suggested."
    return (
        "no_replay_data",
        "Enough trades to judge, but a different stop can't be tested on these trades yet — "
        "the numbers above are still watched for changes.",
    )


def compute_segments() -> dict[str, Any]:
    """Every segment's current numbers. Pure: reads journals, writes nothing."""
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

    segments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        from index_ai.instruments import get_instrument

        rows = _india_rows()
        for inst in SELL_TRAIL_POINTS:
            for lane in ("buy", "sell"):
                dist = (
                    SELL_TRAIL_POINTS[inst]
                    if lane == "sell"
                    else get_instrument(inst).trail_distance_points
                )
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
