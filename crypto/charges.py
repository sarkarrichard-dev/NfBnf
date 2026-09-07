"""Delta trading-cost estimate — exchange fee + GST + observed half-spread.

The index-side lesson (``memory/strategy-findings.md``) applies verbatim: a
friction number that decides the answer must be *measured*, not assumed. So the
half-spread is sampled from the live l2 book on every paper scan
(``memory/crypto_spread_samples.jsonl``) and, once there is enough data, the
median measured spread replaces the per-asset bps fallback.

Not modelled here (tax-return items, not per-trade entry costs): the Indian VDA
tax on crypto gains (30% + 1% TDS).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from crypto.config import CRYPTO_MEMORY

_SAMPLES_PATH = CRYPTO_MEMORY / "crypto_spread_samples.jsonl"
_MIN_SAMPLES = 30          # below this, use the bps fallback
_SAMPLE_WINDOW = 2000      # rows scanned for the running median


def _pct(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Delta Exchange India published derivative fees; GST applies on the fee itself.
TAKER_RATE = _pct("DELTA_TAKER_FEE_PCT", 0.05) / 100.0
MAKER_RATE = _pct("DELTA_MAKER_FEE_PCT", 0.02) / 100.0
GST_ON_FEE = _pct("DELTA_GST_PCT", 18.0) / 100.0

# Fallback half-spread, basis points of price, per asset — used until measured.
# Any symbol not listed falls back to 2.0 bps (see half_spread_usd).
_HALF_SPREAD_BPS = {
    "BTCUSD": _pct("DELTA_HALF_SPREAD_BPS_BTC", 1.0),
    "ETHUSD": _pct("DELTA_HALF_SPREAD_BPS_ETH", 2.0),
    "PAXGUSD": _pct("DELTA_HALF_SPREAD_BPS_PAXG", 1.0),  # gold trades tight
}


def fee_usd(notional_usd: float, *, taker: bool = True) -> float:
    """Exchange fee (incl. GST) for one fill of this notional. Double for round trip."""
    rate = TAKER_RATE if taker else MAKER_RATE
    return abs(float(notional_usd)) * rate * (1.0 + GST_ON_FEE)


# --- measured half-spread -------------------------------------------------

def sample_spread(symbol: str, book: dict[str, Any] | None) -> None:
    """Best-effort: record the observed top-of-book spread. Never raises."""
    try:
        if not book or not book.get("bids") or not book.get("asks"):
            return
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        if not (bid > 0 and ask > bid):
            return
        mid = (bid + ask) / 2.0
        row = {
            "t": int(time.time()),
            "symbol": str(symbol).upper(),
            "bid": bid,
            "ask": ask,
            "half_bps": (ask - bid) / 2.0 / mid * 10_000.0,
        }
        CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
        with _SAMPLES_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def measured_half_spread_bps(symbol: str) -> float | None:
    """Median observed half-spread in bps, or None if too few samples."""
    if not _SAMPLES_PATH.is_file():
        return None
    sym = str(symbol).upper()
    vals: list[float] = []
    try:
        lines = _SAMPLES_PATH.read_text(encoding="utf-8").splitlines()[-_SAMPLE_WINDOW:]
    except OSError:
        return None
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("symbol") == sym and isinstance(r.get("half_bps"), (int, float)):
            vals.append(float(r["half_bps"]))
    if len(vals) < _MIN_SAMPLES:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def half_spread_usd(symbol: str, mark_price: float, *, book: dict | None = None) -> float:
    """Half the bid-ask in USD per unit — measured book > median of samples > bps fallback."""
    if book and book.get("bids") and book.get("asks"):
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        if bid > 0 and ask > bid:
            return (ask - bid) / 2.0
    bps = measured_half_spread_bps(symbol)
    if bps is None:
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
    _SAMPLES_PATH = CRYPTO_MEMORY / "_no_such_spread_samples.jsonl"  # force the bps fallback
    assert abs(fee_usd(1000) - 0.59) < 1e-6, fee_usd(1000)
    assert abs(half_spread_usd("BTCUSD", 60000) - 6.0) < 1e-6  # 1 bp of 60k (fallback)
    assert abs(half_spread_usd("PAXGUSD", 3000) - 0.3) < 1e-9  # 1 bp of 3k
    assert abs(half_spread_usd("SOLUSD", 200) - 0.04) < 1e-9   # 2 bp default
    book = {"bids": [{"price": 100.0}], "asks": [{"price": 100.4}]}
    assert abs(half_spread_usd("BTCUSD", 100, book=book) - 0.2) < 1e-9
    rt = round_trip_cost_usd(6000, "BTCUSD", 60000, 100, 0.001)
    assert rt > 0
    # sampling never raises on junk
    sample_spread("BTCUSD", None)
    sample_spread("BTCUSD", {"bids": [], "asks": []})
    print("crypto.charges self-check ok")
