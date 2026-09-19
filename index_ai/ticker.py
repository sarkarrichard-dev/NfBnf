"""Live price ticker for the dashboard's top strip — index spot + crypto mark.

Dhan's index LTP is real API traffic on the same key the live order path uses,
so it's cached a few seconds here rather than hit on every dashboard poll.
Crypto's own ``ticker()`` already caches itself (5s TTL) — reused as-is.
"""

from __future__ import annotations

import time
from typing import Any

from index_ai.config import AppSettings
from index_ai.dhan import DhanClient
from index_ai.instruments import configured_index_keys, get_instrument

_TTL = 15.0
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, produce):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


def _index_quotes(cfg: AppSettings) -> list[dict[str, Any]]:
    if not cfg.dhan.ready:
        return []

    def fetch() -> list[dict[str, Any]]:
        client = DhanClient(cfg.dhan)
        out = []
        for key in configured_index_keys():
            try:
                q = client.index_ltp(get_instrument(key))
                price = float(q.get("last_price") or 0)
                if price:
                    out.append({"symbol": key, "price": price, "change_pct": None})
            except Exception:
                continue
        return out

    return _cached("index_quotes", _TTL, fetch)


def _crypto_quotes() -> list[dict[str, Any]]:
    from crypto.config import crypto_settings
    from crypto.delta.market_data import ticker

    out = []
    try:
        symbols = crypto_settings().symbols
    except Exception:
        return out
    for sym in symbols:
        try:
            t = ticker(sym)
            price = float(t.get("mark_price") or 0)
            if price:
                out.append(
                    {
                        "symbol": sym,
                        "price": price,
                        "change_pct": round(float(t.get("mark_change_24h") or 0), 2),
                    }
                )
        except Exception:
            continue
    return out


def live_ticker(cfg: AppSettings) -> dict[str, Any]:
    return {"rows": [*_index_quotes(cfg), *_crypto_quotes()]}
