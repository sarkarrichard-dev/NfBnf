from __future__ import annotations

import json
from typing import Any

from nbnf.ml.features import build_features


def _rule_summary(symbol: str, metrics: dict[str, Any], bias: float) -> str:
    parts = [
        f"{symbol}: close={metrics.get('close')}, 1d return={metrics.get('ret_1d')}",
        f"SMA20={metrics.get('sma20')}, SMA50={metrics.get('sma50')}, RSI14={metrics.get('rsi14')}",
        f"Active signals: {', '.join(metrics.get('tags') or [])}",
        f"Model blend score (learned-adjusted bias): {bias:+.3f} (−1 bearish … +1 bullish).",
    ]
    return "\n".join(parts)


def _maybe_llm_narrative(symbol: str, metrics: dict[str, Any], bias: float) -> str | None:
    try:
        from nbnf.llm_remote import chat
    except Exception:
        return None

    try:
        payload = json.dumps({"symbol": symbol, "metrics": metrics, "bias": bias}, default=str)
        return chat(
            [
                {
                    "role": "system",
                    "content": "You are a concise equities analyst. No investment advice; describe data only.",
                },
                {
                    "role": "user",
                    "content": f"Summarize this snapshot in 3 short bullets:\n{payload}",
                },
            ],
            temperature=0.2,
            max_tokens=400,
        ).strip()
    except Exception:
        return None


def blend_bias(metrics: dict[str, Any], tag_emas: dict[str, float]) -> float:
    """
    Combine rule-based tilt with learned per-tag EMA rewards in [-1, 1].
    """
    tags = metrics.get("tags") or []
    rsi = metrics.get("rsi14")
    base = 0.0
    if "uptrend_ma" in tags:
        base += 0.35
    if "downtrend_ma" in tags:
        base -= 0.35
    if "rsi_oversold" in tags:
        base += 0.15
    if "rsi_overbought" in tags:
        base -= 0.15
    if rsi is not None:
        base += max(-0.2, min(0.2, (rsi - 50) / 150))

    learn = 0.0
    if tags:
        for t in tags:
            learn += float(tag_emas.get(t, 0.0))
        learn = max(-0.5, min(0.5, learn / max(1, len(tags))))
    return max(-1.0, min(1.0, base * 0.7 + learn))


def make_finding(
    symbol: str,
    ohlc,
    tag_emas: dict[str, float],
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    metrics, tags = build_features(ohlc)
    metrics["tags"] = tags
    bias = blend_bias(metrics, tag_emas)
    summary = _rule_summary(symbol, metrics, bias)
    narrative = _maybe_llm_narrative(symbol, metrics, bias) if use_llm else None
    if narrative:
        summary = summary + "\n\nLLM summary:\n" + narrative
    return {
        "symbol": symbol,
        "metrics": metrics,
        "bias": bias,
        "summary": summary,
        "tags": tags,
    }
