from __future__ import annotations

from typing import Any

from trading_ai_engine.server import db


def evolution_snapshot(symbol: str | None = None, *, limit: int = 50) -> dict[str, Any]:
    return {
        "summary": db.evolution_summary(symbol),
        "events": db.fetch_evolution_events(symbol=symbol, limit=limit),
    }


def record_feedback_evolution(
    *,
    symbol: str,
    finding_id: str,
    rating: int,
    tag_emas: dict[str, float],
) -> None:
    avg = sum(tag_emas.values()) / max(1, len(tag_emas)) if tag_emas else 0.0
    db.insert_evolution_event(
        symbol=symbol,
        event_type="feedback",
        score_delta=float(avg),
        payload={"finding_id": finding_id, "rating": rating, "tag_emas": tag_emas},
    )
