from __future__ import annotations

from typing import Any

from nbnf.brain.engine import run_brain
from nbnf.market_yfinance import history
from nbnf.ml.ingest import text_digest
from nbnf.server import db
from nbnf.yahoo_study.study import yahoo_deep_study


def run_analyze(
    symbol: str,
    period: str,
    *,
    use_llm: bool = True,
    include_yahoo_deep: bool = True,
    include_ml_digest: bool = True,
) -> dict[str, Any]:
    sym = symbol.strip()
    yahoo_study: dict[str, Any] | None = None
    if include_yahoo_deep:
        yahoo_study = yahoo_deep_study(sym)
        ohlc = history(sym, period="5y", interval="1d")
    else:
        ohlc = history(sym, period=period, interval="1d")
    tag_emas = db.get_tag_emas(sym)
    ml_digest = text_digest() if include_ml_digest else None
    pack = run_brain(sym, ohlc, tag_emas, use_llm=use_llm, ml_digest=ml_digest or None)
    if yahoo_study:
        pack["summary"] = pack["summary"] + "\n\n" + yahoo_study["text_block"]
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
    out: dict[str, Any] = {
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
    if yahoo_study is not None:
        out["yahoo_study"] = yahoo_study
    if ml_digest:
        out["ml_digest_preview"] = ml_digest[:2000] + ("..." if len(ml_digest) > 2000 else "")
        out["ml_digest_explainer"] = (
            "This digest is a capped text summary of profiled local files. "
            "It is not a trained model or full-row retrieval."
        )
    return out
