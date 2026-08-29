"""
Measure the real option bid-ask spread instead of guessing it.

``charges.half_spread_points`` shipped with a hand-picked default (0.75pt for
NIFTY). That guess turned out to be the single most consequential number in the
whole backtest: at 0.75pt the directional-sell lane is unprofitable, at 0.15pt it
is profitable. A parameter that flips the conclusion should be measured, not
assumed.

This samples the live chain and records the observed half-spread —
``(ask - bid) / 2`` in premium points — for the strikes actually traded (near-ATM
short legs and far-OTM wings, which behave differently). ``calibrated_half_spread``
returns the observed median so the model uses reality; until enough samples
exist it returns the default and says so.
"""

from __future__ import annotations

import json
import os
import statistics as st
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist

SAMPLES_PATH = MEMORY_DIR / "spread_samples.jsonl"
MIN_SAMPLES = 30
_MAX_KEEP = 4000
# strikes within this % of spot count as "near" (the short leg); beyond is a wing
NEAR_PCT = 1.5


def _f(v: Any) -> float | None:
    try:
        out = float(v)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


def observe(book: Any, instrument: str, spot: float, *, strikes: list[tuple[float, bool]]) -> int:
    """Record the live half-spread for the given (strike, is_call) pairs.

    Returns how many usable samples were written. Never raises — calibration must
    never be able to break a trading tick.
    """
    rows: list[dict[str, Any]] = []
    for strike, is_call in strikes:
        try:
            q = book.quote(strike, is_call)
        except Exception:
            continue
        if q is None:
            continue
        bid, ask, ltp = _f(q.bid), _f(q.ask), _f(q.ltp)
        if not bid or not ask or ask <= bid or not ltp or ltp <= 0:
            continue
        half = (ask - bid) / 2.0
        moneyness = abs(strike - spot) / max(spot, 1.0) * 100.0
        rows.append({
            "at": now_ist().isoformat(timespec="seconds"),
            "instrument": str(instrument).upper(),
            "strike": float(strike),
            "is_call": bool(is_call),
            "ltp": round(ltp, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "half_spread_pts": round(half, 4),
            "half_spread_pct_of_ltp": round(half / ltp * 100.0, 3),
            "moneyness_pct": round(moneyness, 3),
            "bucket": "near" if moneyness <= NEAR_PCT else "wing",
        })
    if not rows:
        return 0
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with SAMPLES_PATH.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    except Exception:
        return 0
    return len(rows)


def _samples() -> list[dict[str, Any]]:
    if not SAMPLES_PATH.is_file():
        return []
    try:
        lines = SAMPLES_PATH.read_text(encoding="utf-8").splitlines()[-_MAX_KEEP:]
    except Exception:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def summary(instrument: str | None = None) -> dict[str, Any]:
    """Observed half-spread stats, split near-ATM vs wing."""
    rows = _samples()
    if instrument:
        rows = [r for r in rows if r.get("instrument") == str(instrument).upper()]
    out: dict[str, Any] = {"instrument": instrument, "samples": len(rows)}
    if not rows:
        return {**out, "ready": False}
    for bucket in ("near", "wing", "all"):
        sel = rows if bucket == "all" else [r for r in rows if r.get("bucket") == bucket]
        pts = [float(r["half_spread_pts"]) for r in sel if r.get("half_spread_pts") is not None]
        if not pts:
            continue
        out[bucket] = {
            "n": len(pts),
            "median_pts": round(st.median(pts), 4),
            "mean_pts": round(st.fmean(pts), 4),
            "p90_pts": round(sorted(pts)[int(0.9 * (len(pts) - 1))], 4),
        }
    out["ready"] = len(rows) >= MIN_SAMPLES
    out["first_at"] = rows[0].get("at")
    out["last_at"] = rows[-1].get("at")
    return out


def calibrated_half_spread(instrument: str) -> tuple[float, str]:
    """(half-spread in points, source). Observed median once enough samples exist.

    An explicit SLIPPAGE_HALF_SPREAD_POINTS_<KEY> env override always wins, so a
    deliberately conservative backtest is still possible.
    """
    key = str(instrument).upper()
    env = os.getenv(f"SLIPPAGE_HALF_SPREAD_POINTS_{key}")
    if env:
        try:
            return max(0.0, float(env)), "env"
        except ValueError:
            pass
    s = summary(key)
    if s.get("ready") and (s.get("all") or {}).get("median_pts") is not None:
        return float(s["all"]["median_pts"]), f"observed ({s['all']['n']} samples)"
    from index_ai.charges import half_spread_points

    return half_spread_points(key), "default (unmeasured)"


def status() -> dict[str, Any]:
    out = {"min_samples": MIN_SAMPLES, "instruments": {}}
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        hs, src = calibrated_half_spread(key)
        out["instruments"][key] = {
            "half_spread_pts": round(hs, 4), "source": src, **summary(key)
        }
    return out


if __name__ == "__main__":  # ponytail self-check
    class _Q:
        def __init__(self, strike, bid, ask, ltp):
            self.strike, self.bid, self.ask, self.ltp = strike, bid, ask, ltp

    class _Book:
        def quote(self, strike, is_call):
            if strike == 24000:
                return _Q(24000, 119.0, 121.0, 120.0)     # near, 1.0pt half-spread
            if strike == 23000:
                return _Q(23000, 2.0, 2.4, 2.2)           # wing, 0.2pt half-spread
            return None

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        globals()["SAMPLES_PATH"] = Path(td) / "s.jsonl"
        n = observe(_Book(), "NIFTY", 24010.0,
                    strikes=[(24000.0, True), (23000.0, False), (99999.0, True)])
        assert n == 2, n                                   # missing strike skipped
        s = summary("NIFTY")
        assert s["samples"] == 2 and s["ready"] is False    # below MIN_SAMPLES
        assert s["near"]["median_pts"] == 1.0
        assert s["wing"]["median_pts"] == 0.2
        # a crossed/absent book must never produce a sample
        class _Bad:
            def quote(self, *a):
                return _Q(1, 5.0, 4.0, 4.5)
        assert observe(_Bad(), "NIFTY", 24010.0, strikes=[(1.0, True)]) == 0
        os.environ["SLIPPAGE_HALF_SPREAD_POINTS_NIFTY"] = "0.33"
        assert calibrated_half_spread("NIFTY") == (0.33, "env")
        del os.environ["SLIPPAGE_HALF_SPREAD_POINTS_NIFTY"]
        hs, src = calibrated_half_spread("NIFTY")
        assert "default" in src                            # not enough samples yet
    print("spread_calib.py self-check ok")
