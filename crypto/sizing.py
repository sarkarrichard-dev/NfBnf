"""Lot-based position sizing — one universal lot count, 1 lot = 1 Delta contract.

The operator sets ``lots`` once; every symbol trades that many contracts. The
per-symbol money differs because ``contract_value`` differs (BTC 0.001, ETH
0.01, …). Delta perp isolated initial margin is ``notional / leverage`` plus a
small fee buffer; that formula (public data only) is what the paper lane uses,
and ``lot_economics`` exposes it for the dashboard. ``crypto/executor.py``
(Phase 4) confirms against Delta's own ``GET /v2/products/{id}/margin_required``
before a live order.

``deploy_usd`` is an optional per-trade margin cap (0 = no cap). A legacy
capital-first path (``lots=0``) is kept for the backtest helper.
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


def _margin_per_contract(contract: Contract, mark: float, lev: float) -> float:
    return contract.contract_value * mark / lev * _FEE_BUFFER


def lot_economics(
    contract: Contract, mark_price: float, *, leverage: float, fx_usdinr: float = 0.0
) -> dict[str, float | None]:
    """What one lot (= one contract) of this symbol costs. Pure, no I/O."""
    mark = float(mark_price)
    lev = min(max(1.0, float(leverage)), max(1.0, contract.max_leverage))
    if mark <= 0 or contract.contract_value <= 0:
        return {"coin_per_lot": contract.contract_value or None, "notional_per_lot_usd": None,
                "margin_per_lot_usd": None, "margin_per_lot_inr": None}
    notional = contract.contract_value * mark
    margin = _margin_per_contract(contract, mark, lev)
    return {
        "coin_per_lot": contract.contract_value,
        "notional_per_lot_usd": round(notional, 2),
        "margin_per_lot_usd": round(margin, 2),
        "margin_per_lot_inr": round(margin * fx_usdinr, 0) if fx_usdinr else None,
    }


def size_position(
    contract: Contract,
    mark_price: float,
    *,
    leverage: float,
    wallet_usd: float,
    lots: int = 0,
    deploy_usd: float = 0.0,
    allow_min_one: bool = False,
) -> SizingResult:
    lev = min(max(1.0, float(leverage)), max(1.0, contract.max_leverage))
    mark = float(mark_price)
    cap = max(0.0, float(deploy_usd))
    if mark <= 0 or contract.contract_value <= 0:
        return _fail("no usable mark price / contract value", lev=lev)
    lo, hi = _band(contract.symbol)
    if not (lo <= mark <= hi):
        return _fail(f"mark {mark:,.2f} outside the sane band for {contract.symbol}", mark=mark, lev=lev)

    per_contract_notional = contract.contract_value * mark
    margin1 = _margin_per_contract(contract, mark, lev)
    if margin1 <= 0:
        return _fail("computed zero margin per contract", mark=mark, lev=lev)

    safe_wallet = wallet_usd * _WALLET_SAFETY
    min_contracts = max(1, int(math.ceil(contract.min_size)))

    if lots and lots > 0:
        size = max(int(lots), min_contracts)
        need = size * margin1
        if cap and need > cap:
            return _fail(
                f"{size} lot(s) need ${need:,.2f} margin, over the ${cap:,.0f} cap",
                m1=margin1, mark=mark, lev=lev,
            )
        if need > safe_wallet:
            return _fail(
                f"{size} lot(s) need ${need:,.2f}, safe bankroll is ${safe_wallet:,.2f}",
                m1=margin1, mark=mark, lev=lev,
            )
        reason = f"{size} lot(s) × ${margin1:,.2f} margin @ {lev:g}x"
    else:
        # legacy capital-first path (backtest helper) — deploy_usd is the budget
        deploy = max(100.0, cap or 100.0)
        size = int(math.floor(deploy / margin1))
        if size < 1:
            if allow_min_one and margin1 <= min(safe_wallet, deploy * 2.0):
                size = 1
            else:
                return _fail(
                    f"1-contract margin ${margin1:,.2f} exceeds deploy ${deploy:,.0f}",
                    m1=margin1, mark=mark, lev=lev,
                )
        while size > 1 and size * margin1 > safe_wallet:
            size -= 1
        if size * margin1 > safe_wallet:
            return _fail(
                f"insufficient bankroll: need ${margin1:,.2f}, have ${safe_wallet:,.2f}",
                m1=margin1, mark=mark, lev=lev,
            )
        if size < min_contracts:
            return _fail(f"size {size} below Delta minimum {min_contracts}", m1=margin1, mark=mark, lev=lev)
        reason = f"${deploy:,.0f} / ${margin1:,.2f} per contract @ {lev:g}x"

    return SizingResult(
        ok=True,
        size=size,
        reason=reason,
        margin_per_contract_usd=round(margin1, 4),
        margin_total_usd=round(size * margin1, 2),
        notional_usd=round(size * per_contract_notional, 2),
        leverage=lev,
        mark_price=mark,
    )


if __name__ == "__main__":  # self-check
    btc = Contract("BTCUSD", 27, contract_value=0.001, tick_size=0.5, min_size=1, max_leverage=100)
    # lot-based: 2 lots is exactly 2 contracts, period
    r = size_position(btc, 60_000, lots=2, leverage=3, wallet_usd=2000)
    assert r.ok and r.size == 2, r
    assert abs(r.notional_usd - 2 * 60) < 1e-6
    # margin ≈ $20.4/lot → 2 lots ≈ $40.8
    assert abs(r.margin_total_usd - 2 * 60_000 * 0.001 / 3 * _FEE_BUFFER) < 0.02
    # leverage clamped to product max
    assert size_position(btc, 60_000, lots=1, leverage=999, wallet_usd=2000).leverage == 100
    # deploy_usd is a cap: 3 lots need ~$61, a $40 cap rejects
    rc = size_position(btc, 60_000, lots=3, leverage=3, wallet_usd=5000, deploy_usd=40)
    assert not rc.ok and "cap" in rc.reason
    # wallet guard still applies in lot mode
    assert not size_position(btc, 60_000, lots=5, leverage=3, wallet_usd=50).ok
    # new symbol, no built-in band: any mark > 0 accepted (PAXG ~ $3k)
    paxg = Contract("PAXGUSD", 84, contract_value=0.001, tick_size=0.1, min_size=1, max_leverage=100)
    assert size_position(paxg, 3_000, lots=1, leverage=3, wallet_usd=2000).ok
    assert not size_position(paxg, 0, lots=1, leverage=3, wallet_usd=2000).ok
    # lot_economics
    le = lot_economics(btc, 60_000, leverage=3, fx_usdinr=88.0)
    assert le["coin_per_lot"] == 0.001 and le["notional_per_lot_usd"] == 60.0
    assert le["margin_per_lot_usd"] and le["margin_per_lot_inr"]
    assert lot_economics(btc, 0, leverage=3)["margin_per_lot_usd"] is None
    # legacy capital-first path (lots=0) still works for the backtest helper
    rl = size_position(btc, 60_000, leverage=3, wallet_usd=2000, deploy_usd=100)
    assert rl.ok and rl.size == 4
    print("crypto.sizing self-check ok")
