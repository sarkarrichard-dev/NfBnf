from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trading_ai_engine.server.paths import DATA_DIR

_STATE_DIR = DATA_DIR / "Learning"
_STATE_PATH = _STATE_DIR / "self_learning_state.json"
_MAX_OUTCOMES = 80


def _default_state() -> dict[str, Any]:
    return {"outcomes": [], "version": "self_learning_v1"}


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return _default_state()
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _save_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def append_outcome(record: dict[str, Any]) -> None:
    """Append a post-mortem outcome row (bounded list)."""
    state = _load_state()
    out = list(state.get("outcomes") or [])
    out.append(record)
    state["outcomes"] = out[-_MAX_OUTCOMES:]
    _save_state(state)


def learning_loop_status() -> dict[str, Any]:
    """Recent self-learning outcomes (for dashboards and debugging)."""
    state = _load_state()
    outs = list(state.get("outcomes") or [])
    return {"outcomes_stored": len(outs), "last_outcomes": outs[-8:]}


def load_refinement_for_context() -> dict[str, Any]:
    """
    Produce fields merged into ``learning_context`` for the next brain pass.

    ``refinement_score_nudge`` is a small correction (-0.05..0.05) from recent
    post-mortem win rate (not a guarantee of future performance).
    """
    state = _load_state()
    outcomes = list(state.get("outcomes") or [])[-25:]
    trials = [o for o in outcomes if o.get("correct") is not None]
    wins = sum(1 for o in trials if o.get("correct") is True)
    n = len(trials)
    win_rate = (wins / n) if n else 0.5
    # Below 50% win rate in window → slight de-risk tilt; above → mild confidence
    nudge = max(-0.05, min(0.05, (win_rate - 0.5) * 0.12))
    return {
        "refinement_score_nudge": round(nudge, 5),
        "post_mortem_summary": {
            "window_outcomes": len(outcomes),
            "labeled_trials": n,
            "wins": wins,
            "win_rate": round(win_rate, 4) if n else None,
        },
    }
