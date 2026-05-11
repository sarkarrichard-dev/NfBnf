from __future__ import annotations

from typing import Any

import pandas as pd

from trading_ai_engine.brain.types import MLSignals
from trading_ai_engine.ml.market_learn import infer_market_model_score


def infer(metrics: dict[str, Any], tags: list[str], ohlc: pd.DataFrame) -> MLSignals:
    """
    Structural regime + score from price/volume only (CPU, pandas-derived metrics).
    Intentionally separate from feedback EMAs so ML and 'learned bias' can be fused later.
    """
    n = len(ohlc.index) if ohlc is not None else 0
    conf = min(1.0, max(0.2, n / 120.0))

    rsi = metrics.get("rsi14")
    rz = metrics.get("vol_z")

    regime_parts: list[str] = []
    if "uptrend_ma" in tags:
        regime_parts.append("trend_up")
    elif "downtrend_ma" in tags:
        regime_parts.append("trend_down")
    else:
        regime_parts.append("ma_flat")

    if rsi is not None and 40 <= rsi <= 60:
        regime_parts.append("range_like")
    if rz is not None and rz > 2:
        regime_parts.append("stress_vol")

    structural_score = 0.0
    if "uptrend_ma" in tags:
        structural_score += 0.45
    if "downtrend_ma" in tags:
        structural_score -= 0.45
    if "rsi_oversold" in tags:
        structural_score += 0.2
    if "rsi_overbought" in tags:
        structural_score -= 0.2
    if rsi is not None:
        structural_score += max(-0.15, min(0.15, (rsi - 50) / 120))
    if rz is not None and rz > 2:
        structural_score *= 0.85

    market_model = infer_market_model_score(metrics)
    if market_model:
        model_score = float(market_model["score"])
        score = structural_score * 0.55 + model_score * 0.45
        conf = min(1.0, conf + 0.12)
        regime_parts.append("trained_market_model")
    else:
        model_score = None
        score = structural_score
    regime = "+".join(regime_parts) if regime_parts else "unknown"
    score = max(-1.0, min(1.0, score))
    rationale = (
        f"regime={regime}; tags={tags or '[]'}; structural_score={structural_score:+.3f}; "
        f"trained_model_score={model_score if model_score is not None else 'absent'}; "
        f"final_score={score:+.3f}; n_bars={n}"
    )
    return MLSignals(
        regime=regime,
        score=score,
        confidence=conf,
        rationale=rationale,
        tags=list(tags),
    )
