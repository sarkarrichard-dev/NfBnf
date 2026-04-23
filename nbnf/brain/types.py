from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Stance = Literal["bullish", "bearish", "neutral"]
Agreement = Literal["full", "partial", "conflict", "ai_absent"]


@dataclass
class MLSignals:
    """Deterministic, on-device 'ML' view: regime + structural score (no LLM)."""

    regime: str
    score: float
    confidence: float
    rationale: str
    tags: list[str] = field(default_factory=list)
    version: str = "ml_struct_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AIVoice:
    """Remote model interpretation; may be heuristic-filled if API unavailable."""

    stance: Stance
    confidence: float
    focus: list[str]
    caveats: list[str]
    narrative: str
    raw_response: str | None
    version: str = "ai_remote_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusedDecision:
    """Single coordinated decision for the desk and future execution policy."""

    action: Stance
    score: float
    confidence: float
    agreement: Agreement
    rationale: str
    weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
