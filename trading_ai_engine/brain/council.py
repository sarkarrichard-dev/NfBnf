"""
Multi-agent "desk" that debates the same numeric snapshot before a single fused AI voice.

When ``OPENAI_API_KEY`` is set and ``use_llm`` is true, runs a short chain of remote JSON turns
(momentum → risk → contrarian → chair). Without LLM, uses deterministic personas derived from
the structural ML score (educational only — not financial advice).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from trading_ai_engine.brain.jsonutil import extract_json_object
from trading_ai_engine.brain.types import AIVoice, MLSignals, Stance


@dataclass
class AgentOpinion:
    id: str
    label: str
    stance: Stance
    confidence: float
    bullets: list[str]
    note: str
    raw: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("raw") and len(str(d["raw"])) > 800:
            d["raw"] = str(d["raw"])[:800] + "…"
        return d


AGENT_DEFS: tuple[tuple[str, str, str], ...] = (
    (
        "momentum",
        "Momentum / regime desk",
        "Read directional drift vs chop from the numbers only; ignore narratives not in JSON.",
    ),
    (
        "risk",
        "Risk & execution desk",
        "Stress failure modes: volatility, gaps, liquidity, overconfidence. Prefer neutral if unstable.",
    ),
    (
        "contrarian",
        "Contrarian desk",
        "Steel-man the opposite trade to the momentum read implied by ML; stay data-bound.",
    ),
)


def _stance_from_score(s: float) -> Stance:
    if s > 0.12:
        return "bullish"
    if s < -0.12:
        return "bearish"
    return "neutral"


def _compact_data_json(symbol: str, ml: MLSignals, learned_bias: float, metrics: dict[str, Any]) -> str:
    keys = (
        "ret_1d",
        "ret_5d",
        "ret_21d",
        "vol_z",
        "rsi14",
        "macd_hist",
        "market_focus",
        "strat_trend_score",
        "strat_mr_score",
        "atr_pct",
    )
    slim = {k: metrics.get(k) for k in keys if k in metrics}
    payload = {
        "symbol": symbol,
        "ml": ml.to_dict(),
        "learned_bias": learned_bias,
        "metrics_subset": slim,
    }
    return json.dumps(payload, default=str)


def _parse_agent_json(raw: str) -> dict[str, Any] | None:
    data = extract_json_object(raw) or {}
    stance = str(data.get("stance", "neutral")).lower()
    if stance not in ("bullish", "bearish", "neutral"):
        stance = "neutral"
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [str(x)[:160] for x in bullets[:4]]
    note = str(data.get("one_line", data.get("note", ""))).strip()[:400]
    return {"stance": stance, "confidence": conf, "bullets": bullets, "note": note}


def _heuristic_slot(agent_id: str, label: str, ml: MLSignals, metrics: dict[str, Any]) -> AgentOpinion:
    base = float(ml.score)
    volz = float(metrics.get("vol_z") or 0.0)
    if agent_id == "momentum":
        st = _stance_from_score(base)
        bullets = [f"regime={ml.regime}", (ml.rationale or "")[:140], f"ml_score={base:+.3f}"]
        return AgentOpinion(agent_id, label, st, float(ml.confidence) * 0.92, bullets, "Heuristic momentum", None)
    if agent_id == "risk":
        st: Stance = "neutral" if abs(volz) > 1.45 else _stance_from_score(base)
        bullets = [f"vol_z={volz:.2f}", "Paper-only context", "Kill-switch aware"]
        return AgentOpinion(agent_id, label, st, 0.44, bullets, "Heuristic risk", None)
    stc = _stance_from_score(-base * 0.9)
    bullets = ["Opposite framing", "Blind-spot check", f"counter_score={-base:+.3f}"]
    return AgentOpinion(agent_id, label, stc, 0.36, bullets, "Heuristic contrarian", None)


def _llm_agent_turn(
    agent_id: str,
    label: str,
    brief: str,
    data_json: str,
    prior_notes: str,
) -> AgentOpinion | None:
    try:
        from trading_ai_engine.llm_remote import chat
    except Exception:
        return None
    system = (
        "You are one desk agent in a trading workstation. Use ONLY the JSON data — no web search, "
        "no live prices outside the payload. Return ONLY JSON with keys: "
        "stance (bullish|bearish|neutral), confidence (0-1 number), bullets (array of 3 short strings), "
        "one_line (single sentence). No markdown fences."
    )
    user = (
        f"agent_id={agent_id}\nrole={label}\nbrief={brief}\n\nDATA_JSON:\n{data_json}\n\n"
        f"PRIOR_AGENT_NOTES:\n{prior_notes or '(none)'}"
    )
    try:
        raw = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.18,
            max_tokens=240,
        )
    except Exception:
        return None
    parsed = _parse_agent_json(raw)
    if not parsed:
        return None
    return AgentOpinion(
        agent_id,
        label,
        parsed["stance"],  # type: ignore[arg-type]
        parsed["confidence"],
        parsed["bullets"],
        parsed["note"],
        raw[:2500],
    )


def _llm_chair(symbol: str, data_json: str, opinions: list[AgentOpinion]) -> AIVoice | None:
    try:
        from trading_ai_engine.llm_remote import chat
    except Exception:
        return None
    agents_min = [
        {"id": o.id, "stance": o.stance, "confidence": o.confidence, "bullets": o.bullets, "note": o.note}
        for o in opinions
    ]
    system = (
        "You chair a small trading desk. Merge agent JSON into ONE view. Use only supplied data. "
        "Return ONLY JSON with keys: stance (bullish|bearish|neutral), confidence (0-1), "
        "focus (string array max 6), caveats (string array max 6), narrative (two short sentences), "
        "agent_alignment (full|partial|conflict). No markdown."
    )
    user = f"symbol={symbol}\n\nAGENTS_JSON:\n{json.dumps(agents_min, default=str)}\n\nDATA_JSON:\n{data_json}"
    try:
        raw = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.12,
            max_tokens=420,
        )
    except Exception:
        return None
    data = extract_json_object(raw) or {}
    stance_raw = str(data.get("stance", "neutral")).lower()
    if stance_raw not in ("bullish", "bearish", "neutral"):
        stance_raw = "neutral"
    try:
        conf = float(data.get("confidence", 0.55))
    except (TypeError, ValueError):
        conf = 0.55
    conf = max(0.0, min(1.0, conf))
    focus = data.get("focus") or []
    caveats = data.get("caveats") or []
    if not isinstance(focus, list):
        focus = [str(focus)]
    if not isinstance(caveats, list):
        caveats = [str(caveats)]
    focus = [str(x) for x in focus][:8]
    caveats = [str(x) for x in caveats][:8]
    align = str(data.get("agent_alignment", "partial")).lower()
    if align not in ("full", "partial", "conflict"):
        align = "partial"
    caveats.append(f"council_alignment={align}")
    narrative = str(data.get("narrative", "")).strip() or "—"
    return AIVoice(
        stance=stance_raw,  # type: ignore[arg-type]
        confidence=conf,
        focus=focus,
        caveats=caveats,
        narrative=narrative,
        raw_response=raw[:4000],
        version="ai_council_remote_v1",
    )


def _vote_synthesis(symbol: str, opinions: list[AgentOpinion], ml: MLSignals) -> tuple[AIVoice, dict[str, Any]]:
    scores: list[float] = []
    for o in opinions:
        sc = 1.0 if o.stance == "bullish" else (-1.0 if o.stance == "bearish" else 0.0)
        scores.append(sc * max(0.1, o.confidence))
    combined = sum(scores) / max(len(scores), 1)
    stance = _stance_from_score(combined)
    conf = min(0.9, 0.32 + abs(combined) * 0.55)
    stances = {o.stance for o in opinions}
    disagreement = 0.5 if len(stances) >= 3 else (0.28 if len(stances) == 2 else 0.12)
    narrative = (
        f"{symbol}: council vote blends {len(opinions)} desks — "
        + ", ".join(f"{o.id}={o.stance}" for o in opinions)
        + f" (combined={combined:+.3f})."
    )
    caveats = [
        f"council_disagreement={disagreement:.2f}",
        "Heuristic chair (remote chair unavailable or LLM off). Educational only.",
    ]
    report: dict[str, Any] = {
        "mode": "heuristic_chair",
        "agents": [o.to_public_dict() for o in opinions],
        "disagreement": disagreement,
        "combined_signal": combined,
        "structural_ml_regime": ml.regime,
    }
    voice = AIVoice(
        stance=stance,
        confidence=conf,
        focus=["brain_council", ml.regime],
        caveats=caveats,
        narrative=narrative,
        raw_response=None,
        version="ai_council_heuristic_v1",
    )
    return voice, report


def infer_council(
    symbol: str,
    metrics: dict[str, Any],
    ml: MLSignals,
    learned_bias: float,
    *,
    use_llm: bool,
) -> tuple[AIVoice, dict[str, Any]]:
    """
    Return ``(ai_voice_for_fusion, council_report)``.

    The council only **informs** the existing fusion + paper gates; it does not bypass risk rules.
    """
    data_json = _compact_data_json(symbol, ml, learned_bias, metrics)
    prior = ""
    opinions: list[AgentOpinion] = []

    if use_llm:
        for agent_id, label, brief in AGENT_DEFS:
            op = _llm_agent_turn(agent_id, label, brief, data_json, prior)
            if op is None:
                op = _heuristic_slot(agent_id, label, ml, metrics)
            opinions.append(op)
            prior += f"\n{agent_id}: stance={op.stance} conf={op.confidence:.2f} bullets={op.bullets}\n"
        chair = _llm_chair(symbol, data_json, opinions)
        if chair is not None:
            stances = {o.stance for o in opinions}
            disagreement = 0.45 if len(stances) >= 3 else (0.22 if len(stances) == 2 else 0.1)
            report = {
                "mode": "remote",
                "agents": [o.to_public_dict() for o in opinions],
                "disagreement": disagreement,
                "chair_version": chair.version,
            }
            return chair, report

    for agent_id, label, _brief in AGENT_DEFS:
        opinions.append(_heuristic_slot(agent_id, label, ml, metrics))
    voice, report = _vote_synthesis(symbol, opinions, ml)
    report["mode"] = "heuristic"
    return voice, report


def slim_council_for_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """Strip heavy fields before SQLite ``metrics`` JSON."""
    return {
        "mode": report.get("mode"),
        "disagreement": report.get("disagreement"),
        "agents": [
            {k: v for k, v in a.items() if k in ("id", "label", "stance", "confidence", "bullets", "note")}
            for a in (report.get("agents") or [])
            if isinstance(a, dict)
        ],
    }
