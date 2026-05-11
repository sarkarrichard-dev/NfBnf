from __future__ import annotations

from typing import Any

from trading_ai_engine.brain.types import AIVoice, Agreement, FusedDecision, MLSignals, Stance


def _stance_to_score(s: Stance) -> float:
    if s == "bullish":
        return 1.0
    if s == "bearish":
        return -1.0
    return 0.0


def fuse(
    ml: MLSignals,
    ai: AIVoice,
    learned_bias: float,
    learning_context: dict[str, Any] | None = None,
) -> FusedDecision:
    """
    Merge structural ML, learned feedback bias, and AI stance.
    On disagreement, confidence is pulled down and action leans neutral unless one side is very confident.
    """
    feedback_summary = (learning_context or {}).get("feedback_summary") or {}
    feedback_count = int(feedback_summary.get("count") or 0)
    recent_feedback = (learning_context or {}).get("recent_feedback") or []

    # Let feedback earn more influence as rated decisions accumulate, while keeping
    # deterministic price structure as the anchor.
    w_bias = min(0.42, 0.16 + feedback_count * 0.025)
    w_ai = 0.24 if ai.version == "ai_heuristic_v1" else 0.28
    w_ml = max(0.30, 1.0 - w_bias - w_ai)
    total = w_ml + w_bias + w_ai
    w_ml, w_bias, w_ai = w_ml / total, w_bias / total, w_ai / total

    ai_score = _stance_to_score(ai.stance) * max(0.15, ai.confidence)
    structural_only = w_ml * ml.score + w_ai * ai_score
    feedback_effect = w_bias * learned_bias
    combined = structural_only + feedback_effect
    combined = max(-1.0, min(1.0, combined))

    ml_sign = 0 if abs(ml.score) < 0.12 else (1 if ml.score > 0 else -1)
    ai_sign = 0 if ai.stance == "neutral" else (1 if ai.stance == "bullish" else -1)
    bias_sign = 0 if abs(learned_bias) < 0.12 else (1 if learned_bias > 0 else -1)

    signs = [x for x in (ml_sign, ai_sign, bias_sign) if x != 0]
    agreement: Agreement
    if not signs:
        agreement = "partial"
    elif len(set(signs)) == 1:
        agreement = "full"
    elif ai.version == "ai_heuristic_v1":
        agreement = "ai_absent"
    else:
        agreement = "conflict" if len(set(signs)) > 1 else "partial"

    conf_ml = ml.confidence
    conf_parts = [conf_ml, min(1.0, abs(learned_bias) + 0.25), ai.confidence]
    fused_conf = max(0.1, min(1.0, sum(conf_parts) / len(conf_parts)))
    if agreement == "conflict":
        fused_conf *= 0.72
    if agreement == "ai_absent":
        fused_conf *= 0.9

    if combined > 0.18:
        action: Stance = "bullish"
    elif combined < -0.18:
        action = "bearish"
    else:
        action = "neutral"

    rationale = (
        f"fused_score={combined:+.3f}; ml={ml.score:+.3f}; bias={learned_bias:+.3f}; "
        f"ai={ai.stance}@{ai.confidence:.2f}; feedback_effect={feedback_effect:+.3f}; "
        f"feedback_count={feedback_count}; agreement={agreement}"
    )

    return FusedDecision(
        action=action,
        score=combined,
        confidence=fused_conf,
        agreement=agreement,
        rationale=rationale,
        weights={"ml": w_ml, "learned_bias": w_bias, "ai": w_ai},
        feedback_effect=feedback_effect,
        loop_state={
            "feedback_count": feedback_count,
            "avg_rating": float(feedback_summary.get("avg_rating") or 0.0),
            "recent_ratings": [int(x.get("rating") or 0) for x in recent_feedback[:5]],
            "memory_active": feedback_count > 0,
        },
    )
