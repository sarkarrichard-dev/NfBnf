"""
Intraday OI shifts and expiry-day pinning, derived from the live Dhan option chain.

Unlike NSE's participant file (end-of-day), this is genuinely live: the chain
carries per-strike OI on every fetch, so differencing successive snapshots gives
where positions are actually being *built and unwound* during the session.

Two readings come out of it:

  * **OI shift** — writers adding calls above spot is resistance being defended;
    writers adding puts below is support. What matters is the change since the
    open, not the absolute OI, because the absolute number is dominated by
    positions opened days ago.
  * **Pinning** — max-pain and the max-OI strikes. On expiry day, spot tends to
    gravitate toward the strike where the most option value expires worthless,
    because writers hedge toward it. This is a mechanical effect, not a forecast,
    and it is only meaningfully strong on the expiry session itself.

Nothing here predicts direction on its own. It is context that says *where the
book is heavy*, which is a reason to be careful selling into a wall, not a
reason to take a trade.
"""

from __future__ import annotations

import json
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist

SNAP_PATH = MEMORY_DIR / "oi_snapshots.json"
_MAX_SNAPSHOTS = 120


def _f(v: Any, d: float = 0.0) -> float:
    try:
        out = float(v)
        return out if out == out else d
    except (TypeError, ValueError):
        return d


def chain_oi(rows: dict[float, dict[str, Any]]) -> dict[str, dict[float, float]]:
    """{'ce': {strike: oi}, 'pe': {...}} from a parsed chain."""
    out: dict[str, dict[float, float]] = {"ce": {}, "pe": {}}
    for strike, row in rows.items():
        for side in ("ce", "pe"):
            leg = row.get(side) or {}
            oi = leg.get("oi", leg.get("open_interest"))
            if oi is not None:
                out[side][float(strike)] = _f(oi)
    return out


def max_pain(rows: dict[float, dict[str, Any]]) -> float | None:
    """Strike where total intrinsic value of all open options is smallest.

    Classic max-pain: for each candidate expiry price, sum what writers would owe
    across every strike; the minimum is where the least option value pays out.
    """
    oi = chain_oi(rows)
    strikes = sorted(set(oi["ce"]) | set(oi["pe"]))
    if len(strikes) < 3:
        return None
    best, best_pain = None, float("inf")
    for expiry_at in strikes:
        pain = 0.0
        for k in strikes:
            if expiry_at > k:                       # calls at k finish ITM
                pain += (expiry_at - k) * oi["ce"].get(k, 0.0)
            if expiry_at < k:                       # puts at k finish ITM
                pain += (k - expiry_at) * oi["pe"].get(k, 0.0)
        if pain < best_pain:
            best, best_pain = expiry_at, pain
    return best


def pinning(rows: dict[float, dict[str, Any]], spot: float, *, is_expiry_day: bool) -> dict[str, Any]:
    """Max-pain / max-OI walls and how far spot sits from them."""
    oi = chain_oi(rows)
    mp = max_pain(rows)
    max_ce = max(oi["ce"], key=lambda k: oi["ce"][k], default=None)
    max_pe = max(oi["pe"], key=lambda k: oi["pe"][k], default=None)
    dist = ((mp - spot) / spot * 100.0) if (mp and spot) else None
    return {
        "max_pain": mp,
        "max_call_oi_strike": max_ce,       # ceiling writers are defending
        "max_put_oi_strike": max_pe,        # floor writers are defending
        "spot": round(spot, 2) if spot else None,
        "max_pain_distance_pct": round(dist, 3) if dist is not None else None,
        "is_expiry_day": bool(is_expiry_day),
        # pinning only bites on the expiry session, and only when spot is near the pin
        "pin_pressure": bool(is_expiry_day and dist is not None and abs(dist) < 0.35),
    }


def _load_snaps() -> list[dict[str, Any]]:
    if not SNAP_PATH.is_file():
        return []
    try:
        return json.loads(SNAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def record_snapshot(instrument: str, expiry: str, rows: dict[float, dict[str, Any]],
                    spot: float) -> dict[str, Any]:
    """Append a compact OI snapshot for this instrument/expiry and return it."""
    oi = chain_oi(rows)
    snap = {
        "instrument": instrument,
        "expiry": expiry,
        "at": now_ist().isoformat(timespec="seconds"),
        "session": now_ist().date().isoformat(),
        "spot": round(_f(spot), 2),
        "ce_total": round(sum(oi["ce"].values())),
        "pe_total": round(sum(oi["pe"].values())),
    }
    snaps = [s for s in _load_snaps() if s.get("session") == snap["session"]]
    snaps.append(snap)
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        SNAP_PATH.write_text(json.dumps(snaps[-_MAX_SNAPSHOTS:], indent=2), encoding="utf-8")
    except Exception:
        pass
    return snap


def oi_shift(instrument: str) -> dict[str, Any]:
    """Change in total call/put OI since the first snapshot of this session."""
    session = now_ist().date().isoformat()
    snaps = [s for s in _load_snaps()
             if s.get("instrument") == instrument and s.get("session") == session]
    if len(snaps) < 2:
        return {"ready": False, "reason": "need two snapshots this session",
                "ce_change": 0, "pe_change": 0, "writer_bias": "NONE"}
    first, last = snaps[0], snaps[-1]
    ce = last["ce_total"] - first["ce_total"]
    pe = last["pe_total"] - first["pe_total"]
    # more call writing than put writing = writers leaning bearish, and vice versa
    if ce - pe > 0.03 * max(first["ce_total"], 1):
        bias = "CALL_WRITING"
    elif pe - ce > 0.03 * max(first["pe_total"], 1):
        bias = "PUT_WRITING"
    else:
        bias = "BALANCED"
    return {
        "ready": True,
        "snapshots": len(snaps),
        "ce_change": round(ce),
        "pe_change": round(pe),
        "spot_change": round(last["spot"] - first["spot"], 2),
        "writer_bias": bias,
    }


def features(pin: dict[str, Any] | None, shift: dict[str, Any] | None) -> dict[str, float]:
    p, s = pin or {}, shift or {}
    bias = {"PUT_WRITING": 1.0, "CALL_WRITING": -1.0}.get(str(s.get("writer_bias")), 0.0)
    return {
        "max_pain_dist_pct": _f(p.get("max_pain_distance_pct")),
        "pin_pressure": 1.0 if p.get("pin_pressure") else 0.0,
        "is_expiry_day": 1.0 if p.get("is_expiry_day") else 0.0,
        "oi_writer_bias": bias,
    }


if __name__ == "__main__":  # ponytail self-check
    # heavy call OI at 24200, heavy put OI at 23800 -> max pain lands between
    rows = {
        23800.0: {"ce": {"oi": 1000}, "pe": {"oi": 90000}},
        24000.0: {"ce": {"oi": 50000}, "pe": {"oi": 50000}},
        24200.0: {"ce": {"oi": 90000}, "pe": {"oi": 1000}},
    }
    oi = chain_oi(rows)
    assert oi["ce"][24200.0] == 90000 and oi["pe"][23800.0] == 90000
    mp = max_pain(rows)
    assert mp == 24000.0, mp

    pin = pinning(rows, 24010.0, is_expiry_day=True)
    assert pin["max_call_oi_strike"] == 24200.0 and pin["max_put_oi_strike"] == 23800.0
    assert pin["pin_pressure"] is True                      # expiry + spot within 0.35%
    assert pinning(rows, 24010.0, is_expiry_day=False)["pin_pressure"] is False
    far = pinning(rows, 24600.0, is_expiry_day=True)
    assert far["pin_pressure"] is False                     # too far from the pin

    assert max_pain({23800.0: {"ce": {"oi": 1}}}) is None    # not enough strikes
    f = features(pin, {"writer_bias": "PUT_WRITING"})
    assert f["pin_pressure"] == 1.0 and f["oi_writer_bias"] == 1.0
    assert features(None, None)["oi_writer_bias"] == 0.0
    print("oi_flow.py self-check ok — max pain", mp)
