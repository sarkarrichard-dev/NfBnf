from __future__ import annotations

from typing import Any

from nbnf.brain.engine import run_brain
from nbnf.market_yfinance import history
from nbnf.server import db


def run_analyze(symbol: str, period: str, *, use_llm: bool = True) -> dict[str, Any]:
    sym = symbol.strip()
    ohlc = history(sym, period=period, interval="1d")
    tag_emas = db.get_tag_emas(sym)
    pack = run_brain(sym, ohlc, tag_emas, use_llm=use_llm)
    fid = db.insert_finding(
        symbol=sym,
        summary=pack["summary"],
        tags=pack["tags"],
        metrics=pack["metrics"],
        bias=float(pack["bias"]),
    )
    db.insert_brain_decision(
        finding_id=fid,
        symbol=sym,
        ml=pack["ml"],
        ai=pack["ai"],
        fused=pack["brain"],
    )
    snap = db.learning_snapshot(sym)
    return {
        "finding_id": fid,
        "symbol": sym,
        "summary": pack["summary"],
        "metrics": pack["metrics"],
        "bias": pack["bias"],
        "tags": pack["tags"],
        "ml": pack["ml"],
        "ai": pack["ai"],
        "brain": pack["brain"],
        "learning": snap,
    }
