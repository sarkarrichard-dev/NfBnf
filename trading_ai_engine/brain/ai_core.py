from __future__ import annotations

import json
from typing import Any

from trading_ai_engine.brain.jsonutil import extract_json_object
from trading_ai_engine.brain.types import AIVoice, MLSignals, Stance
from trading_ai_engine.ai_voice.persona import TRADING_AI_SYSTEM_INDIA


def _heuristic_ai(metrics: dict[str, Any], ml: MLSignals) -> AIVoice:
    s = ml.score
    if s > 0.2:
        stance: Stance = "bullish"
    elif s < -0.2:
        stance = "bearish"
    else:
        stance = "neutral"
    return AIVoice(
        stance=stance,
        confidence=0.35,
        focus=["structure_only"],
        caveats=[
            "Trading AI Workstation: remote LLM unavailable; stance mirrors structural ML on NSE-style prints.",
        ],
        narrative="Trading AI Workstation local voice (no API).",
        raw_response=None,
        version="ai_heuristic_v1",
    )


def infer(
    symbol: str,
    metrics: dict[str, Any],
    ml: MLSignals,
    data_bias: float,
    *,
    use_llm: bool,
    ml_digest: str | None = None,
    learning_context: dict[str, Any] | None = None,
    heatmap_digest: str | None = None,
) -> AIVoice:
    if not use_llm:
        return _heuristic_ai(metrics, ml)

    try:
        from trading_ai_engine.llm_remote import chat
    except Exception:
        return _heuristic_ai(metrics, ml)

    payload: dict[str, Any] = {
        "symbol": symbol,
        "metrics": metrics,
        "ml": ml.to_dict(),
        "learned_bias": data_bias,
    }
    if learning_context:
        payload["feedback_memory"] = {
            "summary": learning_context.get("feedback_summary") or {},
            "active_tag_memory": [
                {
                    "tag": row.get("tag"),
                    "ema": row.get("ema"),
                    "n": row.get("n"),
                }
                for row in (learning_context.get("symbol_tag_stats") or [])[:12]
            ],
            "recent_rated_decisions": (learning_context.get("recent_feedback") or [])[:5],
        }
    if ml_digest:
        payload["ml_local_datasets_digest"] = ml_digest[:8000]
    if heatmap_digest:
        payload["option_chain_heatmap_digest"] = heatmap_digest[:6000]
    instruction = (
        "Return ONLY a JSON object (no markdown) with keys: "
        "stance (bullish|bearish|neutral), confidence (0-1 number), "
        "focus (string array of short topics), caveats (string array), "
        "narrative (one short paragraph, data-only, no trade advice). "
        "If feedback_memory is present, account for it as learned operator feedback, not truth. "
        "If ml_local_datasets_digest is present, mention how it complements the price snapshot (no promises). "
        "If option_chain_heatmap_digest is present, relate skew/PCR/volume shape to the equity view (no promises)."
    )
    try:
        raw = chat(
            [
                {
                    "role": "system",
                    "content": TRADING_AI_SYSTEM_INDIA + " Output strict JSON only (no markdown fences).",
                },
                {
                    "role": "user",
                    "content": instruction + "\n\nDATA:\n" + json.dumps(payload, default=str),
                },
            ],
            temperature=0.15,
            max_tokens=500,
        )
    except Exception:
        return _heuristic_ai(metrics, ml)

    data = extract_json_object(raw) or {}
    stance_raw = str(data.get("stance", "neutral")).lower()
    if stance_raw not in ("bullish", "bearish", "neutral"):
        stance_final: Stance = "neutral"
    else:
        stance_final = stance_raw  # type: ignore[assignment]

    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    focus = data.get("focus") or []
    caveats = data.get("caveats") or []
    if not isinstance(focus, list):
        focus = [str(focus)]
    if not isinstance(caveats, list):
        caveats = [str(caveats)]
    focus = [str(x) for x in focus][:8]
    caveats = [str(x) for x in caveats][:8]
    narrative = str(data.get("narrative", "")).strip() or "—"

    return AIVoice(
        stance=stance_final,
        confidence=conf,
        focus=focus,
        caveats=caveats,
        narrative=narrative,
        raw_response=raw[:4000],
        version="ai_remote_v1",
    )
