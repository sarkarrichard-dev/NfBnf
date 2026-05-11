from __future__ import annotations

from typing import Any


def dhan_heatmap_snapshot(
    underlying: str,
    *,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """
    Placeholder for Dhan API–backed chain / heatmap snapshots.

    When you integrate Dhan, replace this body to call your feed and return the same
    shape as ``local_option_chain_heatmap`` (``rows``, ``summary``, ``trade_date``, …)
    so ``heatmap_ml_features`` keeps working without dashboard changes.
    """
    return {
        "source": "dhan",
        "underlying": underlying.upper(),
        "trade_date": trade_date,
        "rows": [],
        "available_dates": [],
        "summary": {
            "status": "not_configured",
            "message": (
                "Dhan heatmap adapter not wired yet. Set DHAN credentials and implement "
                "dhan_heatmap_snapshot in trading_ai_engine/market_vision/dhan_heatmap.py, "
                "or use heatmap_source=local for CSV-backed heatmaps."
            ),
        },
    }
