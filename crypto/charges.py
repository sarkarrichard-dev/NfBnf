"""Delta trading-cost estimate — exchange fee + GST + observed half-spread.

Phase 1 skeleton: published-rate defaults, env-overridable. Phase 3 turns
``half_spread`` into a measurement path (sample the l2 book, write a jsonl),
because the index-side lesson (``memory/strategy-findings.md``) is that a
friction number which decides the answer must be measured, not assumed.

Not modelled here (they are tax-return items, not per-trade entry costs):
the Indian VDA tax on crypto gains (30% + 1% TDS).
"""

from __future__ import annotations

import os


def _pct(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Delta Exchange India published derivative fees; GST applies on the fee itself.
TAKER_RATE = _pct("DELTA_TAKER_FEE_PCT", 0.05) / 100.0
MAKER_RATE = _pct("DELTA_MAKER_FEE_PCT", 0.02) / 100.0
GST_ON_FEE = _pct("DELTA_GST_PCT", 18.0) / 100.0

# Fallback half-spread in basis points of price, per asset, until measured.
_HALF_SPREAD_BPS = {
    "BTCUSD": _pct("DELTA_HALF_SPREAD_BPS_BTC", 1.0),
    "ETHUSD": _pct("DELTA_HALF_SPREAD_BPS_ETH", 2.0),
}


def fee_usd(notional_usd: float, *, taker: bool = True) -> float:
    """Exchange fee (incl. GST) for one fill of this notional. Double for round trip."""
    rate = TAKER_RATE if taker else MAKER_RATE
    return abs(float(notional_usd)) * rate * (1.0 + GST_ON_FEE)


def half_spread_usd(symbol: str, mark_price: float, *, book: dict | None = None) -> float:
    """Half the bid-ask, in USD per unit. Measured from ``book`` when given,
    else the per-asset fallback in basis points."""
    if book and book.get("bids") and book.get("asks"):
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        if bid > 0 and ask > bid:
            return (ask - bid) / 2.0
    bps = _HALF_SPREAD_BPS.get(str(symbol).upper(), 2.0)
    return abs(float(mark_price)) * bps / 10_000.0


def round_trip_cost_usd(notional_usd: float, symbol: str, mark_price: float, size: float,
                        contract_value: float, *, book: dict | None = None,
                        taker: bool = True) -> float:
    """Fee (both sides) + slippage (both sides) for opening and closing a position."""
    fee = fee_usd(notional_usd, taker=taker) * 2.0
    slip_per_unit = half_spread_usd(symbol, mark_price, book=book)
    coins = abs(float(size)) * float(contract_value)
    slip = slip_per_unit * coins * 2.0
    return fee + slip


if __name__ == "__main__":  # self-check
    # $1000 notional, taker: 0.05% * 1.18 GST = $0.59 per side
    assert abs(fee_usd(1000) - 0.59) < 1e-6, fee_usd(1000)
    assert abs(half_spread_usd("BTCUSD", 60000) - 6.0) < 1e-6  # 1 bp of 60k
    book = {"bids": [{"price": 100.0}], "asks": [{"price": 100.4}]}
    assert abs(half_spread_usd("BTCUSD", 100, book=book) - 0.2) < 1e-9
    print("crypto.charges self-check ok")
