from __future__ import annotations

from typing import Any, Literal

from trading_ai_engine.market_vision.dhan_heatmap import dhan_heatmap_snapshot
from trading_ai_engine.options.local_chain import local_option_chain_heatmap

HeatmapSource = Literal["auto", "local", "dhan"]


def fetch_heatmap_snapshot(
    underlying: str = "nifty",
    *,
    trade_date: str | None = None,
    source: HeatmapSource = "auto",
) -> dict[str, Any]:
    """
    Single entry point for AIML heatmap input.

    - ``local``: CSV tree under ``Files To Teach AI/<underlying>/``.
    - ``dhan``: stub until you wire the API (returns empty rows + status).
    - ``auto``: prefer local data when present, else Dhan stub.
    """
    u = underlying.strip().lower() or "nifty"
    if source == "local":
        return local_option_chain_heatmap(u, trade_date=trade_date)
    if source == "dhan":
        return dhan_heatmap_snapshot(u, trade_date=trade_date)
    # auto
    local = local_option_chain_heatmap(u, trade_date=trade_date)
    if (local.get("summary") or {}).get("status") == "ok":
        return local
    return dhan_heatmap_snapshot(u, trade_date=trade_date)
