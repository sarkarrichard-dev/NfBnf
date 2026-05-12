from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from trading_ai_engine.secrets_bridge import env_or_local


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
        client_id=env_or_local("DHAN_CLIENT_ID"),
        access_token=env_or_local("DHAN_ACCESS_TOKEN"),
        feed_url=env_or_local("DHAN_FEED_URL", "wss://api-feed.dhan.co") or "wss://api-feed.dhan.co",
        api_base_url=env_or_local("DHAN_API_BASE_URL", "https://api.dhan.co/v2") or "https://api.dhan.co/v2",
        live_trading_enabled=os.environ.get("TRADING_AI_ENABLE_LIVE_TRADING", "").lower() == "true",
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
        "REST LTP batch: POST /v2/marketfeed/ltp (see trading_ai_engine.dhan.quote_client) when credentials + TRADING_AI_DHAN_LTP_MAP are set.",
        "This app will use Dhan for live heatmaps and LTP snapshots before any order placement is enabled.",
        "Live order placement remains blocked by the bot readiness gate.",
    ]
    return data
