"""Fold closed paper outcomes into pattern live stats (good vs bad trades)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from trading_ai_engine.ml.market_learn import MARKET_MODEL_PATH, load_market_model
from trading_ai_engine.server import db


def refresh_pattern_live_overlay() -> dict[str, Any]:
    """
    Scan recent closed paper orders; bucket wins/losses by ``pattern_snapshot`` keys.
    Persists into ``Market Brain Model.json`` as ``pattern_live_overlay`` for gating.
    """
    rows = db.fetch_closed_paper_for_pattern_feedback(limit=500)
    overlay: dict[str, dict[str, dict[str, int]]] = {}
    for r in rows:
        plan = r.get("plan") or {}
        snap = plan.get("pattern_snapshot") or {}
        if not isinstance(snap, dict) or not snap:
            continue
        won = float(r.get("realized_pnl") or 0) > 0
        for iv, fired in snap.items():
            if not isinstance(fired, dict):
                continue
            bucket = overlay.setdefault(str(iv), {})
            for pat in fired:
                cell = bucket.setdefault(str(pat), {"wins": 0, "losses": 0})
                if won:
                    cell["wins"] += 1
                else:
                    cell["losses"] += 1

    model = load_market_model()
    if not model:
        return {"status": "no_model_file", "rows_scanned": len(rows), "overlay_buckets": 0}
    model["pattern_live_overlay"] = overlay
    model["pattern_live_overlay_updated_at"] = datetime.now(timezone.utc).isoformat()
    MARKET_MODEL_PATH.write_text(json.dumps(model, indent=2, default=str), encoding="utf-8")
    return {
        "status": "ok",
        "rows_scanned": len(rows),
        "overlay_intervals": len(overlay),
        "model_path": str(MARKET_MODEL_PATH),
    }
