from __future__ import annotations

import os
from typing import Any, cast

from trading_ai_engine.brain.engine import run_brain
from trading_ai_engine.market_context import extra_strategy_metrics, fetch_global_context_snapshot
from trading_ai_engine.market_yfinance import history
from trading_ai_engine.ml.hf_online_digest import build_hf_online_learning_digest
from trading_ai_engine.ml.ingest import text_digest
from trading_ai_engine.ml.market_learn import load_market_model
from trading_ai_engine.server import db
from trading_ai_engine.trading.derivatives_focus import (
    FOCUS_DERIVATIVES_INTRADAY,
    resolve_brain_ohlc,
)
from trading_ai_engine.trading.paper import plan_from_analysis
from trading_ai_engine.trading.paper_gates import paper_placement_allowed
from trading_ai_engine.yahoo_study.study import yahoo_deep_study
from trading_ai_engine.learning.refinement import load_refinement_for_context
from trading_ai_engine.market_vision.features import heatmap_ml_features, heatmap_text_digest
from trading_ai_engine.market_vision.providers import HeatmapSource, fetch_heatmap_snapshot


def _local_file_digest_allowed() -> bool:
    return (os.environ.get("TRADING_AI_ALLOW_LOCAL_FILE_DIGEST_FOR_BRAIN") or "").lower() in (
        "1",
        "true",
        "yes",
    )


def run_analyze(
    symbol: str,
    period: str,
    *,
    use_llm: bool = True,
    include_yahoo_deep: bool = True,
    include_ml_digest: bool = False,
    include_heatmap: bool = False,
    heatmap_underlying: str = "nifty",
    heatmap_source: str = "auto",
    interval: str = "1d",
    market_focus: str | None = None,
    include_global_context: bool = True,
    include_hf_online_digest: bool = True,
    include_strategy_features: bool = True,
) -> dict[str, Any]:
    sym = symbol.strip()
    focus = (market_focus or os.environ.get("TRADING_AI_MARKET_FOCUS") or "balanced").strip().lower()
    ohlc_period, ohlc_interval = resolve_brain_ohlc(
        market_focus=focus,
        period=period,
        interval=interval,
        include_yahoo_deep=include_yahoo_deep,
    )
    yahoo_study: dict[str, Any] | None = None
    if include_yahoo_deep:
        yahoo_study = yahoo_deep_study(sym)
    ohlc = history(sym, period=ohlc_period, interval=ohlc_interval)
    learning_context = db.learning_context(sym)
    learning_context.update(load_refinement_for_context())
    tag_emas = learning_context["tag_emas"]
    ml_digest = (
        text_digest()
        if include_ml_digest and _local_file_digest_allowed()
        else None
    )

    global_meta: dict[str, Any] = {}
    global_digest: str | None = None
    if include_global_context:
        g_period = (os.environ.get("TRADING_AI_GLOBAL_CONTEXT_PERIOD") or "10d").strip()
        g_interval = (os.environ.get("TRADING_AI_GLOBAL_CONTEXT_INTERVAL") or "1d").strip()
        g_pack = fetch_global_context_snapshot(period=g_period, interval=g_interval)
        global_digest = str(g_pack.get("digest") or "")
        global_meta = {k: v for k, v in g_pack.items() if k != "digest"}

    hf_digest: str | None = None
    hf_meta: dict[str, Any] = {}
    if include_hf_online_digest:
        hf_digest, hf_meta = build_hf_online_learning_digest()

    strat_metrics: dict[str, Any] = {}
    if include_strategy_features:
        strat_metrics = extra_strategy_metrics(ohlc)

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
        extra_metrics=strat_metrics or None,
        online_hf_digest=(hf_digest.strip() if hf_digest else None) or None,
        global_context_digest=(global_digest.strip() if global_digest else None) or None,
    )
    pack["metrics"]["market_focus"] = focus
    pack["metrics"]["ohlc_interval"] = ohlc_interval
    pack["metrics"]["ohlc_period"] = ohlc_period
    pack["metrics"]["ohlc_bars"] = int(len(ohlc))
    if ohlc_interval != "1d":
        pack["summary"] = (
            pack["summary"]
            + f"\n[Chart] {ohlc_interval} bars, lookback={ohlc_period}, rows={len(ohlc)}. "
            "Feature ``ret_1d`` is the prior **bar** return (not a full calendar day)."
        )
    if focus == FOCUS_DERIVATIVES_INTRADAY:
        pack["summary"] = (
            pack["summary"]
            + "\n[F&O / intraday] Heatmap + Yahoo options snapshot (when deep study on) support "
            "options context; paper plan still uses spot-style quantity until lot sizing is wired."
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
    out["online_learning"] = {
        "global_context": global_meta,
        "hf_hub": hf_meta,
        "strategy_features": strat_metrics,
        "local_file_digest_included": bool(ml_digest),
    }
    if ml_digest:
        out["ml_digest_preview"] = ml_digest[:2000] + ("..." if len(ml_digest) > 2000 else "")
        out["ml_digest_explainer"] = (
            "Optional SQLite catalog of locally profiled files (disabled by default for brain learning)."
        )
    if hf_digest:
        out["hf_digest_preview"] = hf_digest[:2000] + ("..." if len(hf_digest) > 2000 else "")
    if global_digest:
        out["global_digest_preview"] = global_digest[:2000] + ("..." if len(global_digest) > 2000 else "")
    return out
