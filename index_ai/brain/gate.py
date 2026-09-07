"""
The single entry gate every lane calls.

Combines the two halves of the brain:
  * regime  — is this lane allowed to trade at all today?
  * model   — is *this* setup above the learned win-probability threshold?

Fails open by design. A missing model, an unproven gate, or an unmappable setup
returns ``allowed=True`` with a reason, so the brain can never silently halt
trading; it only ever removes setups it has earned the right to remove.
"""

from __future__ import annotations

import os
from typing import Any

from index_ai.brain import model as brain_model
from index_ai.brain.regime import RegimeRead, allows


def enabled() -> bool:
    return os.getenv("ENABLE_BRAIN_GATE", "true").strip().lower() in {"1", "true", "yes", "on"}


def check(
    trade_like: dict[str, Any],
    *,
    lane: str,
    regime: RegimeRead | None = None,
) -> dict[str, Any]:
    """``allowed`` plus why. ``trade_like`` is a journal-shaped dict for the setup."""
    if not enabled():
        return {"allowed": True, "reason": "brain gate disabled", "win_probability": None}

    if regime is not None and not allows(regime, lane):
        return {
            "allowed": False,
            "reason": f"regime {regime.regime}: {regime.reason}",
            "regime": regime.regime,
            "win_probability": None,
        }

    verdict = brain_model.score(trade_like)
    return {
        "allowed": bool(verdict["passes"]),
        "reason": verdict["reason"],
        "regime": regime.regime if regime else None,
        "win_probability": verdict["win_probability"],
        "gate": verdict["gate"],
        "armed": verdict["armed"],
    }


def status() -> dict[str, Any]:
    return {"enabled": enabled(), **brain_model.status()}
