"""
India VIX level, percentile, and implied-vol term structure.

Two honest caveats about "VIX term structure":

  * India VIX **futures were delisted by NSE**, so there is no tradable VIX curve
    to read a term structure off. Anything claiming one from VIX alone is fiction.
  * The real, obtainable term structure is in the **option chain itself** — ATM
    implied vol of the near expiry versus the next expiry. That is what
    ``iv_term_structure`` computes, and it is the more useful number anyway
    because it is the vol you actually trade.

Near IV above next IV (backwardation) means the market is paying up for immediate
risk — an event or stress is priced into this week. Near below next (contango) is
the normal calm state. Selling premium into steep backwardation is how option
sellers get hurt, so the ratio is a regime input, not a directional signal.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist

CACHE_PATH = MEMORY_DIR / "vix.json"
_NSE_HOME = "https://www.nseindia.com"
_ALL_INDICES = "https://www.nseindia.com/api/allIndices"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# India VIX regime bands (annualised vol points)
CALM_BELOW = 12.0
ELEVATED_ABOVE = 18.0
STRESSED_ABOVE = 25.0


@dataclass(frozen=True)
class VixRead:
    last: float
    previous_close: float
    change_pct: float
    day_high: float
    day_low: float
    year_high: float
    year_low: float
    percentile_1y: float          # 0-1, where today's level sits in the 1y range
    regime: str                   # CALM | NORMAL | ELEVATED | STRESSED
    fetched_at_ist: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _band(v: float) -> str:
    if v >= STRESSED_ABOVE:
        return "STRESSED"
    if v >= ELEVATED_ABOVE:
        return "ELEVATED"
    if v < CALM_BELOW:
        return "CALM"
    return "NORMAL"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        out = float(v)
        return out if out == out else default
    except (TypeError, ValueError):
        return default


def parse_all_indices(payload: dict[str, Any]) -> VixRead | None:
    rows = payload.get("data") or []
    row = next((r for r in rows if "VIX" in str(r.get("index", "")).upper()), None)
    if not row:
        return None
    last = _f(row.get("last"))
    if last <= 0:
        return None
    yhi, ylo = _f(row.get("yearHigh"), last), _f(row.get("yearLow"), last)
    span = max(yhi - ylo, 1e-6)
    return VixRead(
        last=last,
        previous_close=_f(row.get("previousClose"), last),
        change_pct=_f(row.get("percentChange")),
        day_high=_f(row.get("high"), last),
        day_low=_f(row.get("low"), last),
        year_high=yhi,
        year_low=ylo,
        percentile_1y=round(min(1.0, max(0.0, (last - ylo) / span)), 4),
        regime=_band(last),
        fetched_at_ist=now_ist().isoformat(timespec="seconds"),
    )


def fetch() -> VixRead | None:
    """India VIX from NSE. Needs a homepage hit first to get the session cookie."""
    try:
        with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
            c.get(_NSE_HOME)
            r = c.get(_ALL_INDICES)
            if r.status_code != 200:
                return None
            read = parse_all_indices(r.json())
    except Exception:
        return None
    if read is not None:
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(read.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass
    return read


def cached() -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def load(*, refresh: bool = False) -> dict[str, Any] | None:
    if refresh:
        got = fetch()
        if got:
            return got.to_dict()
    return cached() or (fetch().to_dict() if fetch() else None)


def iv_term_structure(near_atm_iv: float | None, next_atm_iv: float | None) -> dict[str, Any]:
    """ATM IV of the near expiry vs the next one — the term structure you can trade.

    ratio > 1 = backwardation (near vol richer; stress or event priced into this
    week) — the state in which selling near-dated premium tends to go wrong.
    """
    n, f = _f(near_atm_iv), _f(next_atm_iv)
    if n <= 0 or f <= 0:
        return {"near_atm_iv": None, "next_atm_iv": None, "ratio": None,
                "shape": "UNKNOWN", "sell_friendly": None}
    ratio = n / f
    shape = "BACKWARDATION" if ratio > 1.05 else "CONTANGO" if ratio < 0.95 else "FLAT"
    return {
        "near_atm_iv": round(n, 2),
        "next_atm_iv": round(f, 2),
        "ratio": round(ratio, 4),
        "shape": shape,
        # selling near-dated premium is friendlier when the near leg is not
        # dramatically bid relative to the next expiry
        "sell_friendly": bool(ratio <= 1.05),
    }


def features(vix: dict[str, Any] | None, term: dict[str, Any] | None = None) -> dict[str, float]:
    t = term or {}
    if not vix:
        return {"vix_level": 0.0, "vix_pctile": 0.5, "vix_change_pct": 0.0,
                "iv_term_ratio": 1.0, "vix_stressed": 0.0}
    return {
        "vix_level": round(_f(vix.get("last")), 2),
        "vix_pctile": round(_f(vix.get("percentile_1y"), 0.5), 4),
        "vix_change_pct": round(_f(vix.get("change_pct")), 3),
        "iv_term_ratio": round(_f(t.get("ratio"), 1.0), 4),
        "vix_stressed": 1.0 if str(vix.get("regime")) in {"ELEVATED", "STRESSED"} else 0.0,
    }


if __name__ == "__main__":  # ponytail self-check
    payload = {"data": [
        {"index": "NIFTY 50", "last": 24000},
        {"index": "INDIA VIX", "last": 10.66, "previousClose": 11.07, "percentChange": -3.7,
         "high": 11.14, "low": 10.53, "yearHigh": 28.91, "yearLow": 8.72},
    ]}
    v = parse_all_indices(payload)
    assert v is not None and v.last == 10.66 and v.regime == "CALM"
    assert abs(v.percentile_1y - (10.66 - 8.72) / (28.91 - 8.72)) < 1e-3
    assert _band(30) == "STRESSED" and _band(20) == "ELEVATED" and _band(14) == "NORMAL"
    assert parse_all_indices({"data": [{"index": "NIFTY 50", "last": 1}]}) is None

    back = iv_term_structure(18.0, 14.0)
    assert back["shape"] == "BACKWARDATION" and back["sell_friendly"] is False
    con = iv_term_structure(12.0, 14.0)
    assert con["shape"] == "CONTANGO" and con["sell_friendly"] is True
    assert iv_term_structure(None, 14.0)["shape"] == "UNKNOWN"

    f = features(v.to_dict(), con)
    assert f["vix_stressed"] == 0.0 and f["iv_term_ratio"] == con["ratio"]
    assert features(None)["vix_pctile"] == 0.5
    print("vix.py self-check ok")
