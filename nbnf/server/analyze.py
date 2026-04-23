from __future__ import annotations

from typing import Any

from nbnf.market_yfinance import history
from nbnf.ml.findings import make_finding
from nbnf.server import db


def run_analyze(symbol: str, period: str, *, use_llm: bool = True) -> dict[str, Any]:
    sym = symbol.strip()
    ohlc = history(sym, period=period, interval="1d")
    tag_emas = db.get_tag_emas(sym)
    finding = make_finding(sym, ohlc, tag_emas, use_llm=use_llm)
    fid = db.insert_finding(
        symbol=sym,
        summary=finding["summary"],
        tags=finding["tags"],
        metrics=finding["metrics"],
        bias=float(finding["bias"]),
    )
    snap = db.learning_snapshot(sym)
    return {
        "finding_id": fid,
        "symbol": sym,
        "summary": finding["summary"],
        "metrics": finding["metrics"],
        "bias": finding["bias"],
        "tags": finding["tags"],
        "learning": snap,
    }
