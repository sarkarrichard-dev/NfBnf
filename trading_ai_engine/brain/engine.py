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
    if ml_digest:
        summary_lines.append("[Local data catalog digest - profiled files, not trained weights]")
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
        "summary": summary,
    }
