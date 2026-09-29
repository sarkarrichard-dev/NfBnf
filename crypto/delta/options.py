"""Delta Exchange BTC options — live product list + ATM-strike lookup.

Separate from ``products.py`` on purpose: that module filters to
``contract_type == "perpetual_futures"`` only (everything else in ``crypto/``
trades perps). Options have a materially different shape (strike, expiry,
two-sided market) and only one strategy (``crypto/btc_straddle.py``) uses
this, so it isn't folded into the perp product cache.

Symbol format (``reference/openalgo/broker/deltaexchange/database/
master_contract_db.py``): ``C-BTC-<strike>-<DDMMYY>`` / ``P-BTC-<strike>-<DDMMYY>``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from crypto.delta.client import DeltaClient

logger = logging.getLogger(__name__)

_TTL = 30.0  # options list changes as expiries roll — cheap to re-pull, no disk cache needed
_cache: tuple[float, list[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    product_id: int
    strike: float
    is_call: bool
    settlement: datetime
    tick_size: float
    contract_value: float


def _pull(client: DeltaClient) -> list[dict[str, Any]]:
    raw = client.get_public("/v2/products")
    out: list[dict[str, Any]] = []
    for p in raw or []:
        if p.get("contract_type") not in ("call_options", "put_options"):
            continue
        if p.get("state") != "live" or p.get("trading_status") != "operational":
            continue
        underlying = (p.get("underlying_asset") or {}).get("symbol")
        if underlying != "BTC":
            continue
        settlement = p.get("settlement_time")
        if not settlement:
            continue
        out.append(p)
    return out


def _live_products(client: DeltaClient) -> list[dict[str, Any]]:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _TTL:
        return _cache[1]
    fresh = _pull(client)
    _cache = (now, fresh)
    return fresh


def _to_leg(p: dict[str, Any]) -> OptionLeg:
    settlement = datetime.fromisoformat(str(p["settlement_time"]).replace("Z", "+00:00"))
    return OptionLeg(
        symbol=str(p["symbol"]),
        product_id=int(p["id"]),
        strike=float(p.get("strike_price") or 0),
        is_call=p["contract_type"] == "call_options",
        settlement=settlement,
        tick_size=float(p.get("tick_size") or 0),
        contract_value=float(p.get("contract_value") or 0.001),
    )


def nearest_expiry_atm_straddle(
    spot: float, client: DeltaClient | None = None, *, now: datetime | None = None
) -> tuple[OptionLeg, OptionLeg] | None:
    """The call+put closest to ``spot`` on the soonest not-yet-settled BTC
    expiry (Delta's "daily" options — a fresh 24h-ish contract every day).
    None if no live BTC options are listed right now."""
    c = client or DeltaClient()
    now = now or datetime.now(timezone.utc)
    legs = [_to_leg(p) for p in _live_products(c)]
    upcoming = [leg for leg in legs if leg.settlement > now]
    if not upcoming:
        return None
    soonest = min(leg.settlement for leg in upcoming)
    same_expiry = [leg for leg in upcoming if leg.settlement == soonest]
    calls = [leg for leg in same_expiry if leg.is_call]
    puts = [leg for leg in same_expiry if not leg.is_call]
    if not calls or not puts:
        return None
    atm_call = min(calls, key=lambda leg: abs(leg.strike - spot))
    atm_strike = atm_call.strike
    atm_put = min(puts, key=lambda leg: abs(leg.strike - atm_strike))
    return atm_call, atm_put


if __name__ == "__main__":  # self-check — no network, fakes _live_products
    _now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    fake = [
        {  # already settled — must be excluded
            "id": 1,
            "symbol": "C-BTC-80000-290925",
            "contract_type": "call_options",
            "underlying_asset": {"symbol": "BTC"},
            "strike_price": "80000",
            "settlement_time": "2026-09-29T10:00:00Z",
            "tick_size": "0.5",
            "contract_value": "0.001",
        },
        {  # soonest live expiry, ATM
            "id": 2,
            "symbol": "C-BTC-90000-300925",
            "contract_type": "call_options",
            "underlying_asset": {"symbol": "BTC"},
            "strike_price": "90000",
            "settlement_time": "2026-09-30T12:00:00Z",
            "tick_size": "0.5",
            "contract_value": "0.001",
        },
        {
            "id": 3,
            "symbol": "P-BTC-90000-300925",
            "contract_type": "put_options",
            "underlying_asset": {"symbol": "BTC"},
            "strike_price": "90000",
            "settlement_time": "2026-09-30T12:00:00Z",
            "tick_size": "0.5",
            "contract_value": "0.001",
        },
        {  # a further expiry — must be ignored in favour of the soonest
            "id": 4,
            "symbol": "C-BTC-95000-011025",
            "contract_type": "call_options",
            "underlying_asset": {"symbol": "BTC"},
            "strike_price": "95000",
            "settlement_time": "2026-10-01T12:00:00Z",
            "tick_size": "0.5",
            "contract_value": "0.001",
        },
        {  # not BTC — must be ignored
            "id": 5,
            "symbol": "C-ETH-3000-300925",
            "contract_type": "call_options",
            "underlying_asset": {"symbol": "ETH"},
            "strike_price": "3000",
            "settlement_time": "2026-09-30T12:00:00Z",
            "tick_size": "0.1",
            "contract_value": "0.01",
        },
    ]
    globals()["_live_products"] = lambda _client: fake
    pair = nearest_expiry_atm_straddle(89700.0, DeltaClient.__new__(DeltaClient), now=_now)
    assert pair is not None
    call, put = pair
    assert call.symbol == "C-BTC-90000-300925" and put.symbol == "P-BTC-90000-300925"
    assert call.strike == put.strike == 90000.0
    assert call.is_call and not put.is_call

    none_left = nearest_expiry_atm_straddle(
        89700.0,
        DeltaClient.__new__(DeltaClient),
        now=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    assert none_left is None  # every fake expiry is in the past
    print("crypto.delta.options self-check ok")
