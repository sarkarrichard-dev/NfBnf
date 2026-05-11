from __future__ import annotations

import math
from typing import Any


def heatmap_ml_features(heatmap: dict[str, Any]) -> dict[str, float]:
    """
    Flat numeric vector derived from a heatmap snapshot (stable keys for future trainers).

    Works for ``local_option_chain_heatmap`` / Dhan once it matches the same ``rows`` shape.
    """
    rows = heatmap.get("rows") or []
    summary = heatmap.get("summary") or {}
    out: dict[str, float] = {
        "heatmap_strikes": float(len(rows)),
        "heatmap_contract_count": float(heatmap.get("contract_count") or 0),
    }
    pcr = summary.get("pcr_oi")
    try:
        pcr_f = float(pcr) if pcr is not None else 0.0
        if math.isnan(pcr_f):
            pcr_f = 0.0
    except (TypeError, ValueError):
        pcr_f = 0.0
    out["heatmap_pcr_oi"] = pcr_f

    ce_oi = float(summary.get("total_ce_oi") or 0)
    pe_oi = float(summary.get("total_pe_oi") or 0)
    tot = ce_oi + pe_oi
    out["heatmap_ce_oi_share"] = (ce_oi / tot) if tot > 0 else 0.0
    out["heatmap_pe_oi_share"] = (pe_oi / tot) if tot > 0 else 0.0

    vol_ce = 0.0
    vol_pe = 0.0
    for r in rows:
        ce = r.get("CE") or {}
        pe = r.get("PE") or {}
        vol_ce += float(ce.get("volume") or 0)
        vol_pe += float(pe.get("volume") or 0)
    vtot = vol_ce + vol_pe
    out["heatmap_ce_volume_share"] = (vol_ce / vtot) if vtot > 0 else 0.0
    out["heatmap_log_strikes"] = math.log1p(len(rows))

    return out


def heatmap_text_digest(heatmap: dict[str, Any], *, max_chars: int = 2500) -> str:
    """Human + LLM readable summary of the snapshot."""
    summary = heatmap.get("summary") or {}
    lines = [
        f"Heatmap source={heatmap.get('source')} underlying={heatmap.get('underlying')} "
        f"trade_date={heatmap.get('trade_date')}",
        f"Summary: {summary}",
        f"ML features (numeric): {heatmap_ml_features(heatmap)}",
    ]
    text = "\n".join(lines)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")
