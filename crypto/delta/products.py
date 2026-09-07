"""Delta perpetual-contract master — product_id, contract_value, tick, min size.

One ``GET /v2/products`` call, filtered to the perps we trade, cached to
``memory/crypto_products.json`` and refreshed daily. Field mapping follows
``reference/openalgo/broker/deltaexchange/database/master_contract_db.py``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from crypto.config import CRYPTO_MEMORY, PERP_SYMBOLS
from crypto.delta.client import DeltaClient

logger = logging.getLogger(__name__)

_CACHE_PATH = CRYPTO_MEMORY / "crypto_products.json"
_MAX_AGE_SECONDS = 24 * 3600
_mem: dict[str, Any] | None = None


@dataclass(frozen=True)
class Contract:
    symbol: str
    product_id: int
    contract_value: float   # units of the underlying coin per contract (e.g. 0.001 BTC)
    tick_size: float
    min_size: float         # minimum order size in contracts
    max_leverage: float

    @property
    def usable(self) -> bool:
        return self.product_id > 0 and self.contract_value > 0


def _pull(client: DeltaClient) -> dict[str, Any]:
    raw = client.get_public("/v2/products")
    want = set(PERP_SYMBOLS)
    contracts: dict[str, Any] = {}
    for p in raw or []:
        sym = str(p.get("symbol") or "")
        if sym not in want or p.get("contract_type") != "perpetual_futures":
            continue
        if p.get("state") != "live" or p.get("trading_status") != "operational":
            logger.warning("Delta %s not live/operational — skipping", sym)
            continue
        specs = p.get("product_specs") or {}
        cv = float(p.get("contract_value") or 0)
        if cv <= 0:
            logger.error("Delta %s has no contract_value — cannot size it, skipping", sym)
            continue
        contracts[sym] = {
            "symbol": sym,
            "product_id": int(p.get("id") or 0),
            "contract_value": cv,
            "tick_size": float(p.get("tick_size") or 0),
            "min_size": float(specs.get("min_order_size") or 1),
            "max_leverage": float(
                specs.get("max_leverage")
                or p.get("default_leverage")
                or p.get("max_leverage")
                or 100
            ),
        }
    return {"fetched_at": time.time(), "contracts": contracts}


def _load_disk() -> dict[str, Any] | None:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_disk(blob: dict[str, Any]) -> None:
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    os.replace(tmp, _CACHE_PATH)


def _blob(client: DeltaClient | None = None, *, force: bool = False) -> dict[str, Any]:
    global _mem
    now = time.time()
    if not force and _mem and now - float(_mem.get("fetched_at", 0)) < _MAX_AGE_SECONDS:
        return _mem
    if not force:
        disk = _load_disk()
        if disk and now - float(disk.get("fetched_at", 0)) < _MAX_AGE_SECONDS:
            _mem = disk
            return _mem
    fresh = _pull(client or DeltaClient())
    _save_disk(fresh)
    _mem = fresh
    return _mem


def refresh(force: bool = True) -> list[Contract]:
    return [_to_contract(c) for c in _blob(force=force)["contracts"].values()]


def _to_contract(d: dict[str, Any]) -> Contract:
    return Contract(
        symbol=d["symbol"],
        product_id=int(d["product_id"]),
        contract_value=float(d["contract_value"]),
        tick_size=float(d["tick_size"]),
        min_size=float(d["min_size"]),
        max_leverage=float(d["max_leverage"]),
    )


def get(symbol: str, client: DeltaClient | None = None) -> Contract | None:
    d = _blob(client)["contracts"].get(str(symbol).upper())
    return _to_contract(d) if d else None


def all_contracts(client: DeltaClient | None = None) -> dict[str, Contract]:
    return {k: _to_contract(v) for k, v in _blob(client)["contracts"].items()}


if __name__ == "__main__":  # self-check — no network (uses a fake blob)
    _mem = {
        "fetched_at": time.time(),
        "contracts": {
            "BTCUSD": {
                "symbol": "BTCUSD", "product_id": 27, "contract_value": 0.001,
                "tick_size": 0.5, "min_size": 1, "max_leverage": 100,
            }
        },
    }
    c = get("btcusd")
    assert c and c.usable and c.product_id == 27 and c.contract_value == 0.001
    assert get("DOGEUSD") is None
    print("crypto.delta.products self-check ok")
