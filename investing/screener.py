"""First-cut NSE stock screener — an MVP, not the final design.

This is deliberately NOT the "dual time-horizon, buy/hold + short & long-term
levels" design from memory/future-investing-section.md — that needs its own
real design work (technical levels for a short-term trade AND a separate
long-term thesis on the same name). This first cut only answers a narrower
question: of the F&O-eligible NSE universe, which names look cheap and
healthy by plain value/quality ratios. Treat the score as a starting filter
to look at, never a buy signal.
"""

from __future__ import annotations

from investing import openbb_bridge, universe


def _score(f: dict) -> float | None:
    """Higher = cheaper and healthier, plain ratios only. None if data's missing."""
    pe = f.get("pe_ratio")
    margin = f.get("profit_margin")
    if pe is None or pe <= 0 or margin is None:
        return None
    score = 10.0 / pe                        # cheaper P/E scores higher
    score += (margin or 0.0) * 5.0           # profitable
    score += (f.get("revenue_growth") or 0.0) * 3.0   # growing revenue
    score -= (f.get("debt_to_equity") or 0.0) * 0.02  # penalise heavy debt
    return round(score, 4)


def run_screen(*, limit: int | None = None) -> list[dict]:
    symbols = sorted(universe.load_or_fetch_universe())
    if limit:
        symbols = symbols[:limit]
    fundamentals = openbb_bridge.fetch_fundamentals(symbols)

    rows = []
    for sym in symbols:
        f = fundamentals.get(sym) or {}
        if "error" in f:
            continue
        score = _score(f)
        if score is None:
            continue
        rows.append({"symbol": sym, "score": score, **f})
    rows.sort(key=lambda r: -r["score"])
    return rows


if __name__ == "__main__":  # self-check — fake bridge, no network, no venv needed
    fake = {
        "GOODCO": {"pe_ratio": 12.0, "debt_to_equity": 20.0, "profit_margin": 0.18,
                   "revenue_growth": 0.15},
        "BADCO": {"pe_ratio": 60.0, "debt_to_equity": 200.0, "profit_margin": 0.02,
                  "revenue_growth": -0.1},
        "NODATA": {"error": "no data"},
    }
    openbb_bridge.fetch_fundamentals = lambda symbols, **_: fake
    universe.load_or_fetch_universe = lambda **_: dict.fromkeys(fake, {})
    rows = run_screen()
    assert [r["symbol"] for r in rows] == ["GOODCO", "BADCO"], rows
    assert rows[0]["score"] > rows[1]["score"]
    print("investing.screener self-check ok:", rows)
