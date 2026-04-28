from __future__ import annotations

from typing import Any

from nbnf.dhan.config import load_dhan_config


def market_feed_status() -> dict[str, Any]:
    """
    Dhan live-feed integration status.

    Official Dhan v2 feed uses WebSocket URL:
    wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2

    Subscriptions are JSON requests, while responses are binary packets. The actual streaming
    loop is intentionally not started automatically from the web UI until credentials are present
    and a subscription universe is chosen.
    """
    cfg = load_dhan_config()
    return {
        "provider": "DhanHQ",
        "ready": cfg.data_ready,
        "feed_url": cfg.feed_url,
        "version": 2,
        "auth_type": 2,
        "supports": ["ticker", "quote", "full", "oi", "market_depth"],
        "limits": {
            "connections_per_user": 5,
            "instruments_per_connection": 5000,
            "subscribe_batch_size": 100,
        },
        "next_step": (
            "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN, then map symbols to Dhan security IDs."
            if not cfg.data_ready
            else "Credentials detected. Next map watchlist/options to Dhan security IDs."
        ),
    }
