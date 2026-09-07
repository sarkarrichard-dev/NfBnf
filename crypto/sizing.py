"""Capital-first position sizing — deploy $X (>= $100) at leverage L → whole contracts.

Delta perp isolated initial margin is ``notional / leverage`` plus a small fee
buffer; for the paper lane that formula (public data only) is what decides the
contract count. ``crypto/executor.py`` (Phase 4) confirms against Delta's own
``GET /v2/products/{id}/margin_required`` before a live order.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from crypto.delta.products import Contract

_WALLET_SAFETY = 0.90  # keep 10% of the bankroll free
_FEE_BUFFER = 1.02     # margin headroom over the raw notional/leverage figure

# Plausible mark-price band per asset — rejects a corrupted feed or a units
# mix-up before it silently sizes the position 10x off. Wide on purpose. Only
# BTC/ETH have a built-in band; any other symbol just needs mark > 0 (the
# deploy-cap and 90% wallet guard bound the risk regardless), unless the
# operator sets CRYPTO_SANE_MIN_<SYM> / CRYPTO_SANE_MAX_<SYM>.
_SANE_PRICE = {
    "BTCUSD": (1_000.0, 10_000_000.0),
    "ETHUSD": (10.0, 1_000_000.0),
}
_warned: set[str] = set()


def _band(symbol: str) -> tuple[float, float]:
    sym = symbol.upper()
    if sym in _SANE_PRICE:
        return _SANE_PRICE[sym]
    import os

    def _env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    lo = _env(f"CRYPTO_SANE_MIN_{sym}", 0.0)
    hi = _env(f"CRYPTO_SANE_MAX_{sym}", float("inf"))
    if lo == 0.0 and hi == float("inf") and sym not in _warned:
        _warned.add(sym)
        logging.getLogger(__name__).info(
            "crypto sizing: no sane-price band for %s — accepting any mark > 0", sym
        )
    return lo, hi


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    size: int
    reason: str
    margin_per_contract_usd: float
    margin_total_usd: float
    notional_usd: float
    leverage: float
    mark_price: float


def _fail(reason: str, m1: float = 0.0, mark: float = 0.0, lev: float = 0.0) -> SizingResult:
    return SizingResult(False, 0, reason, m1, 0.0, 0.0, lev, mark)


def size_position(
    contract: Contract,
    mark_price: float,
    *,
    deploy_usd: float,
    leverage: float,
    wallet_usd: float,
    allow_min_one: bool = False,
) -> SizingResult:
    deploy = max(100.0, float(deploy_usd))
    lev = min(max(1.0, float(leverage)), max(1.0, contract.max_leverage))
    mark = float(mark_price)
    if mark <= 0 or contract.contract_value <= 0:
        return _fail("no usable mark price / contract value", lev=lev)
    lo, hi = _band(contract.symbol)
    if not (lo <= mark <= hi):
        return _fail(f"mark {mark:,.2f} outside the sane band for {contract.symbol}", mark=mark, lev=lev)

    per_contract_notional = contract.contract_value * mark
    margin1 = per_contract_notional / lev * _FEE_BUFFER
    if margin1 <= 0:
        return _fail("computed zero margin per contract", mark=mark, lev=lev)

    size = int(math.floor(deploy / margin1))
    if size < 1:
        # allow_min_one still refuses to deploy far past the operator's intent
        if allow_min_one and margin1 <= min(wallet_usd * _WALLET_SAFETY, deploy * 2.0):
            size = 1
        else:
            return _fail(
                f"1-contract margin ${margin1:,.2f} exceeds deploy ${deploy:,.0f}",
                m1=margin1, mark=mark, lev=lev,
            )

    # wallet guard — shrink until the basket fits inside the safe bankroll
    while size > 1 and size * margin1 > wallet_usd * _WALLET_SAFETY:
        size -= 1
    if size * margin1 > wallet_usd * _WALLET_SAFETY:
        return _fail(
            f"insufficient bankroll: need ${margin1:,.2f}, have ${wallet_usd * _WALLET_SAFETY:,.2f}",
            m1=margin1, mark=mark, lev=lev,
        )

    min_contracts = max(1, int(math.ceil(contract.min_size)))
    if size < min_contracts:
        return _fail(f"size {size} below Delta minimum {min_contracts}", m1=margin1, mark=mark, lev=lev)

    return SizingResult(
        ok=True,
        size=size,
        reason=f"${deploy:,.0f} / ${margin1:,.2f} per contract @ {lev:g}x",
        margin_per_contract_usd=round(margin1, 4),
        margin_total_usd=round(size * margin1, 2),
        notional_usd=round(size * per_contract_notional, 2),
        leverage=lev,
        mark_price=mark,
    )


if __name__ == "__main__":  # self-check
    btc = Contract("BTCUSD", 27, contract_value=0.001, tick_size=0.5, min_size=1, max_leverage=100)
    # $100 at 3x, BTC $60k: notional/contract = $60, margin ≈ $20.4 → 4 contracts
    r = size_position(btc, 60_000, deploy_usd=100, leverage=3, wallet_usd=2000)
    assert r.ok and r.size == 4, r
    assert abs(r.notional_usd - 4 * 60) < 1e-6
    # leverage clamped to product max
    r2 = size_position(btc, 60_000, deploy_usd=100, leverage=999, wallet_usd=2000)
    assert r2.leverage == 100
    # deploy floored at 100
    r3 = size_position(btc, 60_000, deploy_usd=10, leverage=3, wallet_usd=2000)
    assert r3.ok and r3.size == 4
    # bankroll too small
    r4 = size_position(btc, 60_000, deploy_usd=100, leverage=3, wallet_usd=10)
    assert not r4.ok and "bankroll" in r4.reason
    # a new symbol with no built-in band: any mark > 0 is accepted (PAXG ~ $3k)
    paxg = Contract("PAXGUSD", 84, contract_value=0.001, tick_size=0.1, min_size=1, max_leverage=100)
    r5 = size_position(paxg, 3_000, deploy_usd=100, leverage=3, wallet_usd=2000)
    assert r5.ok and r5.size >= 1
    assert not size_position(paxg, 0, deploy_usd=100, leverage=3, wallet_usd=2000).ok
    print("crypto.sizing self-check ok")
