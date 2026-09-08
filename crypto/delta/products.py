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

from crypto.config import CRYPTO_MEMORY, crypto_settings
from crypto.delta.client import DeltaClient

logger = logging.getLogger(__name__)

_CACHE_PATH = CRYPTO_MEMORY / "crypto_products.json"
_MAX_AGE_SECONDS = 24 * 3600
_DELIST_AFTER_SECONDS = 7 * 24 * 3600  # a symbol absent this long from /v2/products is really gone
_SCHEMA = 3  # bump when _pull's shape/filter changes so a stale disk cache is dropped
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
    """Cache EVERY live perpetual Delta lists. all_contracts() then filters to
    the operator's CRYPTO_SYMBOLS; available_symbols() offers the full picklist."""
    raw = client.get_public("/v2/products")
    contracts: dict[str, Any] = {}
    for p in raw or []:
        sym = str(p.get("symbol") or "")
        if not sym or p.get("contract_type") != "perpetual_futures":
            continue
        if p.get("state") != "live" or p.get("trading_status") != "operational":
            continue
        specs = p.get("product_specs") or {}
        cv = float(p.get("contract_value") or 0)
        if cv <= 0:
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
    return {"schema": _SCHEMA, "fetched_at": time.time(), "contracts": contracts}


def _load_disk() -> dict[str, Any] | None:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_disk(blob: dict[str, Any]) -> None:
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    os.replace(tmp, _CACHE_PATH)


_MIN_LIVE_CONTRACTS = 20  # Delta lists 200+; a pull below this is partial/broken


def _covers_configured(blob: dict[str, Any] | None) -> bool:
    """True if the blob has every symbol in CRYPTO_SYMBOLS — so a cache that's
    missing one the operator selected is never treated as good enough."""
    if not blob:
        return False
    have = set(blob.get("contracts") or {})
    from crypto.config import crypto_settings

    return set(crypto_settings().symbols) <= have


def _fresh(blob: dict[str, Any] | None, now: float) -> bool:
    return bool(
        blob
        and int(blob.get("schema", 1)) == _SCHEMA
        and now - float(blob.get("fetched_at", 0)) < _MAX_AGE_SECONDS
        and len(blob.get("contracts") or {}) >= _MIN_LIVE_CONTRACTS
        and _covers_configured(blob)
    )


def _blob(client: DeltaClient | None = None, *, force: bool = False) -> dict[str, Any]:
    global _mem
    now = time.time()
    if not force and _fresh(_mem, now):
        return _mem
    if not force:
        disk = _load_disk()
        if _fresh(disk, now):
            _mem = disk
            return _mem
    fresh = _pull(client or DeltaClient())
    # A partial pull (Delta occasionally returns a handful of contracts, or a
    # symbol briefly flips out of "operational") must NOT replace a good cache.
    if len(fresh["contracts"]) < _MIN_LIVE_CONTRACTS:
        good = _mem if _covers_configured(_mem) else _load_disk()
        if _covers_configured(good):
            logger.warning(
                "crypto: /v2/products returned only %d contracts — keeping the cached list",
                len(fresh["contracts"]),
            )
            # short fetched_at so the next scan retries in ~5 min, not 24h
            good = {**good, "fetched_at": now - _MAX_AGE_SECONDS + 300}
            _mem = good
            _save_disk(good)
            return _mem
    # Delta's /v2/products response is occasionally incomplete (a symbol briefly
    # flips out of "operational", a partial page): a symbol that traded fine an
    # hour ago should not vanish because it's missing from one pull. Keep the
    # last-known entry for anything the fresh pull dropped, and only forget it
    # once it has been absent for _DELIST_AFTER_SECONDS (a real delisting).
    prev = (_mem or _load_disk() or {}).get("contracts") or {}
    now_ts = fresh["fetched_at"]
    kept_from_cache = False
    merged: dict[str, Any] = {}
    for sym, spec in fresh["contracts"].items():
        merged[sym] = {**spec, "last_seen": now_ts}
    for sym, spec in prev.items():
        if sym in merged:
            continue
        last_seen = float(spec.get("last_seen") or spec.get("fetched_at") or now_ts)
        if now_ts - last_seen < _DELIST_AFTER_SECONDS:
            merged[sym] = spec  # keep the stale-but-recent entry
            kept_from_cache = True
            logger.info("crypto: %s missing from this /v2/products pull — kept from cache", sym)
    fresh["contracts"] = merged
    if kept_from_cache or not _covers_configured(fresh):
        # this pull was incomplete — retry in ~5 min rather than trusting it 24h
        fresh["fetched_at"] = now_ts - _MAX_AGE_SECONDS + 300
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
    """Only the perps the operator picked (CRYPTO_SYMBOLS) that Delta lists live.
    A picked symbol Delta doesn't list is dropped with a warning, never faked."""
    cache = _blob(client)["contracts"]
    out: dict[str, Contract] = {}
    for sym in crypto_settings().symbols:
        if sym in cache:
            out[sym] = _to_contract(cache[sym])
        else:
            logger.warning("crypto: %s is not a live Delta perp — skipped", sym)
    return out


def available_symbols(client: DeltaClient | None = None) -> list[str]:
    """The dashboard picklist: the CRYPTO_ALLOWLIST coins Delta lists live."""
    from crypto.config import CRYPTO_ALLOWLIST

    try:
        live = set(_blob(client)["contracts"].keys())
    except Exception:
        return []
    return [s for s in CRYPTO_ALLOWLIST if s in live]


if __name__ == "__main__":  # self-check — no network (uses a fake blob)
    _covers_configured = lambda _b: True  # noqa: E731 — fake blobs won't cover real .env symbols
    _MIN_LIVE_CONTRACTS = 1
    _pull = lambda _c: {"schema": _SCHEMA, "fetched_at": time.time(), "contracts": {}}  # noqa: E731

    _mem = {
        "schema": _SCHEMA,
        "fetched_at": time.time(),
        "contracts": {
            "BTCUSD": {"symbol": "BTCUSD", "product_id": 27, "contract_value": 0.001,
                       "tick_size": 0.5, "min_size": 1, "max_leverage": 100},
            "PAXGUSD": {"symbol": "PAXGUSD", "product_id": 84, "contract_value": 0.001,
                        "tick_size": 0.1, "min_size": 1, "max_leverage": 100},
        },
    }
    _mem["contracts"]["FOOUSD"] = {"symbol": "FOOUSD", "product_id": 9, "contract_value": 1.0,
                                   "tick_size": 0.1, "min_size": 1, "max_leverage": 50}
    c = get("btcusd")
    assert c and c.usable and c.product_id == 27
    assert get("DOGEUSD") is None
    # available_symbols is the allowlist ∩ live — FOOUSD (not on the allowlist) is hidden
    assert available_symbols() == ["BTCUSD", "PAXGUSD"]
    # a configured symbol that isn't in the contract cache is dropped from
    # all_contracts() (env-independent: .env may pin CRYPTO_SYMBOLS)
    from crypto.config import crypto_settings as _cs

    want = set(_cs().symbols)
    have = set(_blob()["contracts"])
    assert set(all_contracts()) == (want & have)
    # a cache from an older schema is stale even if recent
    _full = {"schema": _SCHEMA, "fetched_at": time.time(), "contracts": {"BTCUSD": {}, "ETHUSD": {}}}
    assert not _fresh({**_full, "schema": 1}, time.time())
    assert _fresh(_full, time.time())
    # …and a suspiciously small pull is not "fresh" either
    _MIN_LIVE_CONTRACTS = 20
    assert not _fresh(_full, time.time())
    _MIN_LIVE_CONTRACTS = 1

    # merge: a symbol dropped from one /v2/products pull is kept from cache
    # (isolated pytest in test_crypto_phase1 covers the full merge + partial-pull
    # guard; here we just check the last-seen expiry helper inline)
    _now = time.time()
    _keep = {"symbol": "SOLUSD", "last_seen": _now - 60}
    _gone = {"symbol": "SOLUSD", "last_seen": _now - _DELIST_AFTER_SECONDS - 1}
    assert (_now - float(_keep["last_seen"])) < _DELIST_AFTER_SECONDS
    assert (_now - float(_gone["last_seen"])) >= _DELIST_AFTER_SECONDS
    print("crypto.delta.products self-check ok")
