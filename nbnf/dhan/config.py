from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DhanConfig:
    client_id: str | None
    access_token: str | None
    feed_url: str = "wss://api-feed.dhan.co"
    api_base_url: str = "https://api.dhan.co/v2"
    live_trading_enabled: bool = False

    @property
    def data_ready(self) -> bool:
        return bool(self.client_id and self.access_token)


def load_dhan_config() -> DhanConfig:
    return DhanConfig(
        client_id=os.environ.get("DHAN_CLIENT_ID") or None,
        access_token=os.environ.get("DHAN_ACCESS_TOKEN") or None,
        feed_url=os.environ.get("DHAN_FEED_URL", "wss://api-feed.dhan.co"),
        api_base_url=os.environ.get("DHAN_API_BASE_URL", "https://api.dhan.co/v2"),
        live_trading_enabled=os.environ.get("NBNF_ENABLE_LIVE_TRADING", "").lower() == "true",
    )


def dhan_readiness() -> dict[str, Any]:
    cfg = load_dhan_config()
    data = asdict(cfg)
    data["access_token"] = "***configured***" if cfg.access_token else None
    data["data_ready"] = cfg.data_ready
    data["live_trading_enabled"] = False
    data["mode"] = "data_feed_ready" if cfg.data_ready else "waiting_for_credentials"
    data["notes"] = [
        "Dhan live market feed is WebSocket-based and returns binary market packets.",
        "This app will use Dhan for data/heatmaps before any order placement is enabled.",
        "Live order placement remains blocked by the bot readiness gate.",
    ]
    return data
