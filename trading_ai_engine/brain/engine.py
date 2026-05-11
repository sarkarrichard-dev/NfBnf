from __future__ import annotations

from typing import Any

import pandas as pd

from trading_ai_engine.brain import ai_core, fusion, ml_core
from trading_ai_engine.ai_voice.persona import TAGLINE
from trading_ai_engine.ml.features import build_features
from trading_ai_engine.ml.findings import blend_bias


def run_brain(
    symbol: str,
    ohlc: pd.DataFrame,
    tag_emas: dict[str, float],
    *,
    use_llm: bool = True,
    ml_digest: str | None = None,
    learning_context: dict[str, Any] | None = None,
    heatmap_context: dict[str, Any] | None = None,
    extra_metrics: dict[str, Any] | None = None,
    online_hf_digest: str | None = None,
    global_context_digest: str | None = None,
    dhan_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One pass: features → ML signals → AI voice → fused decision.
    Summary text is suitable for persistence alongside metrics.
    """
    metrics, tags = build_features(ohlc)
    metrics["tags"] = tags
    if heatmap_context and heatmap_context.get("features"):
        for k, v in heatmap_context["features"].items():
            metrics[k] = v
    if extra_metrics:
        for k, v in extra_metrics.items():
            metrics[k] = v
    learned_bias = blend_bias(metrics, tag_emas, learning_context)

    ml = ml_core.infer(metrics, tags, ohlc)
    ai = ai_core.infer(
        symbol,
        metrics,
        ml,
        learned_bias,
        use_llm=use_llm,
        ml_digest=ml_digest,
        learning_context=learning_context,
        heatmap_digest=(heatmap_context or {}).get("digest") if heatmap_context else None,
        online_hf_digest=online_hf_digest,
        global_context_digest=global_context_digest,
        dhan_quote_digest=(dhan_context or {}).get("digest") if dhan_context else None,
    )
    fused = fusion.fuse(ml, ai, learned_bias, learning_context)

    loop_state = fused.loop_state
    memory_lines = [
        f"[Learning loop] active={loop_state.get('memory_active')} "
        f"feedback_count={loop_state.get('feedback_count')} "
        f"avg_rating={loop_state.get('avg_rating', 0.0):+.3f} "
        f"feedback_effect={fused.feedback_effect:+.3f}",
    ]
    if learning_context and learning_context.get("symbol_tag_stats"):
        memory_lines.append("[Symbol tag memory]")
        for row in learning_context.get("symbol_tag_stats", [])[:8]:
            memory_lines.append(
                f"  {row.get('tag')}: ema={float(row.get('ema') or 0.0):+.3f} "
                f"n={int(row.get('n') or 0)}"
            )

    summary_lines = [
        f"=== {TAGLINE} // Brain // {symbol} ===",
        f"[ML {ml.version}] regime={ml.regime} score={ml.score:+.3f} conf={ml.confidence:.2f}",
        f"  {ml.rationale}",
        f"[AI {ai.version}] stance={ai.stance} conf={ai.confidence:.2f} focus={ai.focus}",
        f"  narrative: {ai.narrative}",
        f"[Fused] action={fused.action} score={fused.score:+.3f} conf={fused.confidence:.2f} "
        f"agreement={fused.agreement}",
        f"  {fused.rationale}",
        f"[Learned bias from feedback EMAs] {learned_bias:+.3f}",
        *memory_lines,
    ]
    if online_hf_digest:
        summary_lines.append("[Hugging Face Hub — streaming row samples, online only]")
        summary_lines.append(online_hf_digest[:5000] + ("..." if len(online_hf_digest) > 5000 else ""))
    if global_context_digest:
        summary_lines.append("[Global cross-asset Yahoo snapshot]")
        summary_lines.append(global_context_digest[:4000] + ("..." if len(global_context_digest) > 4000 else ""))
    if dhan_context and (dhan_context.get("digest") or "").strip():
        summary_lines.append("[Dhan LTP / quote (when mapped)]")
        d = str(dhan_context.get("digest") or "")
        summary_lines.append(d[:3500] + ("..." if len(d) > 3500 else ""))
    if ml_digest:
        summary_lines.append("[Optional local file catalog digest — SQLite ingest, not Hub]")
        summary_lines.append(ml_digest[:6000] + ("..." if len(ml_digest) > 6000 else ""))
    if heatmap_context and heatmap_context.get("digest"):
        summary_lines.append("[Option-chain heatmap digest]")
        d = str(heatmap_context["digest"])
        summary_lines.append(d[:4000] + ("..." if len(d) > 4000 else ""))
    summary = "\n".join(summary_lines)

    return {
        "symbol": symbol,
        "metrics": metrics,
        "tags": tags,
        "bias": learned_bias,
        "ml": ml.to_dict(),
        "ai": ai.to_dict(),
        "brain": fused.to_dict(),
        "learning_context": learning_context or {},
        "heatmap_context": heatmap_context or {},
        "dhan_context": dhan_context or {},
        "summary": summary,
    }
