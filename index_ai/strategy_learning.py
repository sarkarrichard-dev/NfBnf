"""The learning layer — what each strategy is telling us, and when the system
may act on it.

Richard, 2026-09-10: the ML should "keep learning from the first 10-15 trades
itself... improving the entry exit and the strategy... but make sure it has
enough data to make the updates. I don't want it to screw up something that
works."

So this is a **confidence ladder**, not an auto-tuner:

    watching   (< 15 trades)   collect only — no output beyond the count
    observing  (15-39)         flag patterns in the entries; still no changes
    ready      (>= 40, >= 15 trading days)  the tuner may *suggest* a parameter
                                            change for a human to approve

A strategy that is **net-positive over its recent trades is frozen** — the tuner
will never touch it, only observe. Nothing here changes a live strategy; the
suggestion engine (later) posts to the dashboard for approval.

Reads the same journals as ``strategy_performance`` (filtered to the data
epoch). No trade behaviour depends on it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable

from index_ai.data_epoch import data_epoch
from index_ai.market_clock import now_ist_iso
from index_ai.strategy_performance import _after_epoch

WATCH_MAX = 15  # below this: collect only
OBSERVE_MAX = 40  # below this: observe, no suggestions
READY_MIN_DAYS = 15  # …and this many distinct trading days
FREEZE_LOOKBACK = 20  # net-positive over the last N recent trades → frozen
MIN_BUCKET = 4  # don't flag a feature bucket with fewer trades than this
MIN_SIDE = 5  # …or a side skew with fewer than this each side


def _state(n_trades: int, n_days: int) -> str:
    if n_trades < WATCH_MAX:
        return "watching"
    if n_trades < OBSERVE_MAX or n_days < READY_MIN_DAYS:
        return "observing"
    return "ready"


def _next_step(state: str, n_trades: int) -> str:
    if state == "watching":
        return f"{WATCH_MAX - n_trades} more trades → start flagging entry patterns"
    if state == "observing":
        return f"{OBSERVE_MAX - n_trades} more trades (and {READY_MIN_DAYS}+ trading days) → the tuner may suggest changes"
    return "the tuner may propose a parameter change here — you approve each one"


def _win_rate(pnls: list[float]) -> float | None:
    return round(sum(1 for p in pnls if p > 0) / len(pnls), 3) if pnls else None


def _frozen(pnls_recent: list[float]) -> bool:
    """Net-positive over the recent window → locked, the tuner won't touch it."""
    return len(pnls_recent) >= 5 and sum(pnls_recent) > 0


def _bucket_flags(
    rows: list[dict[str, Any]], feature: str, label: Callable[[Any], str | None]
) -> list[str]:
    """The best and worst value of one categorical feature, if each has enough
    trades and they differ by more than 20 points of win rate."""
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = label(r.get(feature))
        if v is not None:
            by[v].append(r["_pnl"])
    ranked = sorted(
        ((k, _win_rate(v), len(v)) for k, v in by.items() if len(v) >= MIN_BUCKET),
        key=lambda x: x[1] or 0.0,
    )
    if len(ranked) < 2:
        return []
    worst, best = ranked[0], ranked[-1]
    if (best[1] or 0) - (worst[1] or 0) < 0.20:
        return []
    return [
        f"{feature}={best[0]}: {best[1] * 100:.0f}% win ({best[2]}) — "
        f"{feature}={worst[0]}: {worst[1] * 100:.0f}% win ({worst[2]})"
    ]


def _observations(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []

    # side skew — the most common real problem ("your longs lose")
    longs = [r["_pnl"] for r in rows if r.get("_side") == "long"]
    shorts = [r["_pnl"] for r in rows if r.get("_side") == "short"]
    if len(longs) >= MIN_SIDE and len(shorts) >= MIN_SIDE:
        wl, ws = _win_rate(longs), _win_rate(shorts)
        if wl is not None and ws is not None and abs(wl - ws) >= 0.20:
            lo, hi = ("long", "short") if wl < ws else ("short", "long")
            out.append(
                f"{hi} entries {(max(wl, ws)) * 100:.0f}% win, {lo} entries {(min(wl, ws)) * 100:.0f}% — "
                f"the {lo} side is the drag"
            )

    # time of day — worst 3h block vs the rest
    by_block: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        h = r.get("_hour")
        if h is not None:
            by_block[h // 3].append(r["_pnl"])
    blocks = [(b, _win_rate(v), len(v)) for b, v in by_block.items() if len(v) >= MIN_BUCKET]
    if len(blocks) >= 2:
        blocks.sort(key=lambda x: x[1] or 0.0)
        b, wr, n = blocks[0]
        rest = [p for r in rows if (r.get("_hour") or 0) // 3 != b for p in [r["_pnl"]]]
        rw = _win_rate(rest)
        if wr is not None and rw is not None and rw - wr >= 0.20:
            out.append(
                f"entries {b * 3:02d}:00-{b * 3 + 3:02d}:00: {wr * 100:.0f}% win ({n}) vs {rw * 100:.0f}% the rest of the day"
            )

    # categorical features carried on the journal row
    out += _bucket_flags(rows, "cpr_regime", lambda v: str(v) if v else None)
    out += _bucket_flags(rows, "cpr_width_class", lambda v: str(v) if v else None)
    out += _bucket_flags(rows, "ema_aligned", lambda v: str(v) if v else None)
    out += _bucket_flags(rows, "cpr_virgin", lambda v: {True: "virgin", False: "tested"}.get(v))
    return out


def _india_rows() -> dict[tuple[str, str], list[dict[str, Any]]]:
    from index_ai.learning import recent_trades
    from index_ai.strategies.strategy_router import trade_lane

    epoch = data_epoch()
    bull = {"BUY_CALL", "SELL_BULL_PUT_SPREAD"}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in recent_trades(limit=1_000_000):
        if t.get("pnl") is None or not _after_epoch(t.get("created_at"), epoch):
            continue
        sig = t.get("signal") or {}
        mode = str(sig.get("strategy_mode") or "").strip()
        strat = (
            mode if mode and mode not in {"wait", "conflict"} else f"{trade_lane(t.get('action'))}"
        )
        inst = str(t.get("instrument") or "?")
        act = str(t.get("action") or "").upper()
        groups[(strat, inst)].append(
            {
                "_pnl": float(t["pnl"]),
                "_side": "long" if act in bull else "short",
                "_hour": int(str(t.get("created_at") or "0000-00-00T00")[11:13] or 0),
                "_day": str(t.get("created_at") or "")[:10],
                "cpr_regime": sig.get("cpr_regime"),
                "cpr_width_class": sig.get("cpr_width_class"),
                "cpr_virgin": sig.get("cpr_virgin"),
                "ema_aligned": sig.get("ema_aligned"),
            }
        )
    return groups


def _crypto_rows() -> dict[tuple[str, str], list[dict[str, Any]]]:
    from crypto.journal import JOURNAL_PATH

    epoch = data_epoch()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if not JOURNAL_PATH.is_file():
        return groups
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not _after_epoch(r.get("closed_at") or r.get("day"), epoch):
            continue
        feat = r.get("features") or {}
        groups[(str(r.get("strategy") or "?"), str(r.get("asset") or "?"))].append(
            {
                "_pnl": float(r.get("pnl_usd") or 0.0),
                "_side": "long" if r.get("side") == "long" else "short",
                "_hour": int(feat.get("entry_hour_utc") or 0),
                "_day": str(r.get("day") or "")[:10],
            }
        )
    return groups


def _report_for(
    groups: dict[tuple[str, str], list[dict[str, Any]]], venue: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (strat, inst), rows in sorted(groups.items()):
        pnls = [r["_pnl"] for r in rows]
        n_days = len({r["_day"] for r in rows if r["_day"]})
        state = _state(len(rows), n_days)
        recent = pnls[:FREEZE_LOOKBACK]  # recent_trades / journal are newest-first-ish
        out.append(
            {
                "venue": venue,
                "strategy": strat,
                "instrument": inst,
                "trades": len(rows),
                "trading_days": n_days,
                "state": state,
                "frozen": _frozen(recent),
                "win_rate": _win_rate(pnls),
                "net": round(sum(pnls), 2),
                "observations": _observations(rows) if state != "watching" else [],
                "next_step": _next_step(state, len(rows)),
            }
        )
    return out


def learning_report() -> dict[str, Any]:
    india = _report_for(_india_rows(), "india")
    crypto = _report_for(_crypto_rows(), "crypto")
    return {
        "generated_at": now_ist_iso(),
        "epoch": data_epoch(),
        "india": india,
        "crypto": crypto,
        "ladder": {
            "watching": f"< {WATCH_MAX} trades — collect only",
            "observing": f"{WATCH_MAX}-{OBSERVE_MAX} — flag entry patterns, no changes",
            "ready": f">= {OBSERVE_MAX} trades and {READY_MIN_DAYS}+ days — tuner may suggest, you approve",
            "frozen": "net-positive recently → the tuner won't touch it",
        },
    }


if __name__ == "__main__":  # self-check — synthetic rows exercise the ladder + a side-skew flag
    rows = [
        {
            "_pnl": 100.0,
            "_side": "short",
            "_hour": 10,
            "_day": f"2026-09-{d:02d}",
            "cpr_regime": "TRENDING_BEAR",
            "cpr_width_class": None,
            "cpr_virgin": None,
            "ema_aligned": None,
        }
        for d in range(1, 23)
    ] + [
        {
            "_pnl": -80.0,
            "_side": "long",
            "_hour": 14,
            "_day": f"2026-10-{d:02d}",
            "cpr_regime": "TRENDING_BULL",
            "cpr_width_class": None,
            "cpr_virgin": None,
            "ema_aligned": None,
        }
        for d in range(1, 23)
    ]
    rep = _report_for({("x", "NIFTY"): rows}, "india")[0]
    assert rep["state"] == "ready", rep
    assert any("side is the drag" in o for o in rep["observations"]), rep["observations"]
    assert _state(5, 2) == "watching" and _state(20, 3) == "observing"
    assert _frozen([10.0, 10.0, -5.0, 8.0, 3.0]) is True
    assert _frozen([-10.0, -10.0, 5.0]) is False
    print("index_ai.strategy_learning self-check ok —", rep["observations"][0])
