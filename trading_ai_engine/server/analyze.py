from __future__ import annotations

from typing import Any, cast

from trading_ai_engine.brain.engine import run_brain
from trading_ai_engine.market_yfinance import history
from trading_ai_engine.ml.ingest import text_digest
from trading_ai_engine.ml.market_learn import load_market_model
from trading_ai_engine.server import db
from trading_ai_engine.trading.paper import plan_from_analysis
from trading_ai_engine.trading.paper_gates import paper_placement_allowed
from trading_ai_engine.yahoo_study.study import yahoo_deep_study
from trading_ai_engine.learning.refinement import load_refinement_for_context
from trading_ai_engine.market_vision.features import heatmap_ml_features, heatmap_text_digest
from trading_ai_engine.market_vision.providers import HeatmapSource, fetch_heatmap_snapshot


def run_analyze(
    symbol: str,
    period: str,
    *,
    use_llm: bool = True,
    include_yahoo_deep: bool = True,
    include_ml_digest: bool = True,
    include_heatmap: bool = False,
    heatmap_underlying: str = "nifty",
    heatmap_source: str = "auto",
) -> dict[str, Any]:
    sym = symbol.strip()
    yahoo_study: dict[str, Any] | None = None
    if include_yahoo_deep:
        yahoo_study = yahoo_deep_study(sym)
        ohlc = history(sym, period="5y", interval="1d")
    else:
        ohlc = history(sym, period=period, interval="1d")
    learning_context = db.learning_context(sym)
    learning_context.update(load_refinement_for_context())
    tag_emas = learning_context["tag_emas"]
    ml_digest = text_digest() if include_ml_digest else None
    heatmap_context: dict[str, Any] | None = None
    if include_heatmap:
        src_raw = (heatmap_source or "auto").strip().lower()
        src: HeatmapSource = cast(
            HeatmapSource, src_raw if src_raw in ("auto", "local", "dhan") else "auto"
        )
        snap = fetch_heatmap_snapshot(
            heatmap_underlying.strip().lower() or "nifty",
            trade_date=None,
            source=src,
        )
        heatmap_context = {
            "features": heatmap_ml_features(snap),
            "digest": heatmap_text_digest(snap),
            "underlying": heatmap_underlying,
            "trade_date": snap.get("trade_date"),
            "summary": snap.get("summary"),
            "heatmap_source": src,
        }
    pack = run_brain(
        sym,
        ohlc,
        tag_emas,
        use_llm=use_llm,
        ml_digest=ml_digest or None,
        learning_context=learning_context,
        heatmap_context=heatmap_context,
    )
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
        "learning_context": pack["learning_context"],
        "heatmap_context": pack.get("heatmap_context") or {},
        "learning": snap,
    }
    out["trade_plan"] = plan_from_analysis(out)
    if out["trade_plan"].get("eligible"):
        ok, gate_reason, gate_meta = paper_placement_allowed(plan=out["trade_plan"])
        if not ok:
            tp = dict(out["trade_plan"])
            tp["eligible"] = False
            tp["vetoes"] = list(tp.get("vetoes") or []) + [gate_reason]
            tp["gate_meta"] = gate_meta
            out["trade_plan"] = tp
    mm = load_market_model()
    if mm:
        out["model_version"] = mm.get("version")
        out["model_trained_at"] = mm.get("trained_at")
    if yahoo_study is not None:
        out["yahoo_study"] = yahoo_study
    if ml_digest:
        out["ml_digest_preview"] = ml_digest[:2000] + ("..." if len(ml_digest) > 2000 else "")
        out["ml_digest_explainer"] = (
            "This digest is a capped text summary of profiled local files. "
            "It is not a trained model or full-row retrieval."
        )
    return out
