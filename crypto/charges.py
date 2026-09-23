"""Delta trading-cost estimate — exchange fee + GST + observed half-spread +
funding.

The index-side lesson (``memory/strategy-findings.md``) applies verbatim: a
friction number that decides the answer must be *measured*, not assumed. So the
half-spread is sampled from the live l2 book on every paper scan
(``memory/crypto_spread_samples.jsonl``) and, once there is enough data, the
median measured spread replaces the per-asset bps fallback. Funding (below)
follows the same discipline: Delta exposes no historical-funding-rate
endpoint, so the live ``funding_rate`` ticker field is sampled every scan
(``memory/crypto_funding_samples.jsonl``) and a trade's funding cost is
reconstructed from the samples nearest each settlement it was open across —
never a guessed constant.

Not modelled here (tax-return items, not per-trade entry costs): the Indian VDA
tax on crypto gains (30% + 1% TDS).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from crypto._util import env_float as _pct
from crypto.config import CRYPTO_MEMORY
from index_ai.market_clock import IST

_SAMPLES_PATH = CRYPTO_MEMORY / "crypto_spread_samples.jsonl"
_MIN_SAMPLES = 30  # below this, use the bps fallback
_SAMPLE_WINDOW = 2000  # rows scanned for the running median

_FUNDING_SAMPLES_PATH = CRYPTO_MEMORY / "crypto_funding_samples.jsonl"
_FUNDING_WINDOW = 2000  # rows scanned when looking up a rate near a settlement

# Delta settles funding 3x/day at these IST clock times — confirmed against
# Delta's own docs (2026-09-20): "Funding will now be exchanged once every 8
# hours ... 5:30am, 1:30pm and 9:30pm [IST]".
_FUNDING_TIMES_IST: tuple[tuple[int, int], ...] = ((5, 30), (13, 30), (21, 30))


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
    "XAUTUSD": _pct("DELTA_HALF_SPREAD_BPS_XAUT", 1.0),  # gold trades tight
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


def round_trip_cost_usd(
    notional_usd: float,
    symbol: str,
    mark_price: float,
    size: float,
    contract_value: float,
    *,
    book: dict | None = None,
    taker: bool = True,
) -> float:
    """Fee (both sides) + slippage (both sides) for opening and closing a position."""
    fee = fee_usd(notional_usd, taker=taker) * 2.0
    slip_per_unit = half_spread_usd(symbol, mark_price, book=book)
    coins = abs(float(size)) * float(contract_value)
    slip = slip_per_unit * coins * 2.0
    return fee + slip


# --- measured funding ------------------------------------------------------


def sample_funding_rate(symbol: str, funding_rate: float | None) -> None:
    """Best-effort: record the live ticker's funding_rate field. Never raises."""
    try:
        if funding_rate is None:
            return
        row = {"t": int(time.time()), "symbol": str(symbol).upper(), "rate": float(funding_rate)}
        CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
        with _FUNDING_SAMPLES_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _funding_rate_near(symbol: str, when_ts: float) -> float | None:
    """The sampled funding_rate closest in time to when_ts (before or after —
    there's no historical endpoint, so nearest sample is the best estimate)."""
    if not _FUNDING_SAMPLES_PATH.is_file():
        return None
    sym = str(symbol).upper()
    try:
        lines = _FUNDING_SAMPLES_PATH.read_text(encoding="utf-8").splitlines()[-_FUNDING_WINDOW:]
    except OSError:
        return None
    best: float | None = None
    best_dist: float | None = None
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("symbol") != sym:
            continue
        dist = abs(float(r["t"]) - when_ts)
        if best_dist is None or dist < best_dist:
            best, best_dist = float(r["rate"]), dist
    return best


def _funding_crossings_utc(entry_utc: datetime, exit_utc: datetime) -> list[datetime]:
    """UTC instants of every Delta funding settlement strictly between entry
    and exit (a position open across a settlement owes/receives that one)."""
    day = entry_utc.astimezone(IST).date()
    end_day = exit_utc.astimezone(IST).date()
    out: list[datetime] = []
    while day <= end_day:
        for h, m in _FUNDING_TIMES_IST:
            ts = datetime(day.year, day.month, day.day, h, m, tzinfo=IST).astimezone(timezone.utc)
            if entry_utc < ts <= exit_utc:
                out.append(ts)
        day += timedelta(days=1)
    return out


def funding_cost_usd(
    *,
    symbol: str,
    side: str,
    notional_usd: float,
    entry_time: str | None,
    exit_time: str | None,
) -> float:
    """Net funding paid (positive) or received (negative) across every Delta
    settlement the position was open for, from sampled live funding_rate —
    Delta gives no historical-funding endpoint, so this can't be exact for a
    position opened before sampling started, but it's real measured data, not
    an assumed constant. Zero if timestamps are missing/unparseable or no
    sample exists near a crossing (never fabricates a number it can't measure).

    Sign: Delta's own definition — a positive funding_rate means longs pay
    shorts. A long position's cost scales +rate; a short's scales -rate (a
    receipt, which lowers cost / raises P&L when subtracted upstream).
    """
    if not entry_time or not exit_time:
        return 0.0
    try:
        entry_utc = datetime.fromisoformat(str(entry_time))
        exit_utc = datetime.fromisoformat(str(exit_time))
    except ValueError:
        return 0.0
    if entry_utc.tzinfo is None or exit_utc.tzinfo is None or exit_utc <= entry_utc:
        return 0.0
    direction = 1.0 if str(side).lower() == "long" else -1.0
    total = 0.0
    for ts in _funding_crossings_utc(entry_utc, exit_utc):
        rate = _funding_rate_near(symbol, ts.timestamp())
        if rate is None:
            continue
        # Delta's ticker funding_rate is in PERCENT per 8h (BTC's base 0.01
        # means 0.01%). It was multiplied as a fraction until 2026-09-23,
        # overstating every funding charge 100x (~$152 recorded vs ~$1.52).
        total += rate / 100.0 * float(notional_usd) * direction
    return round(total, 6)


if __name__ == "__main__":  # self-check
    _SAMPLES_PATH = CRYPTO_MEMORY / "_no_such_spread_samples.jsonl"  # force the bps fallback
    assert abs(fee_usd(1000) - 0.59) < 1e-6, fee_usd(1000)
    assert abs(half_spread_usd("BTCUSD", 60000) - 6.0) < 1e-6  # 1 bp of 60k (fallback)
    assert abs(half_spread_usd("PAXGUSD", 3000) - 0.3) < 1e-9  # 1 bp of 3k
    assert abs(half_spread_usd("SOLUSD", 200) - 0.04) < 1e-9  # 2 bp default
    book = {"bids": [{"price": 100.0}], "asks": [{"price": 100.4}]}
    assert abs(half_spread_usd("BTCUSD", 100, book=book) - 0.2) < 1e-9
    rt = round_trip_cost_usd(6000, "BTCUSD", 60000, 100, 0.001)
    assert rt > 0
    # sampling never raises on junk
    sample_spread("BTCUSD", None)
    sample_spread("BTCUSD", {"bids": [], "asks": []})

    # --- funding self-check ---
    _FUNDING_SAMPLES_PATH = CRYPTO_MEMORY / "_no_such_funding_samples.jsonl"
    # a window with exactly one settlement (05:30 IST) inside it
    entry = datetime(2026, 9, 20, 0, 0, tzinfo=IST).astimezone(timezone.utc)
    exit_ = datetime(2026, 9, 20, 8, 0, tzinfo=IST).astimezone(timezone.utc)
    crossings = _funding_crossings_utc(entry, exit_)
    assert len(crossings) == 1, crossings
    assert crossings[0].astimezone(IST).hour == 5 and crossings[0].astimezone(IST).minute == 30

    # no sample near the crossing -> zero, never a guessed number
    assert (
        funding_cost_usd(
            symbol="ZZZNOSAMPLE",
            side="long",
            notional_usd=1000,
            entry_time=entry.isoformat(),
            exit_time=exit_.isoformat(),
        )
        == 0.0
    )

    # sample a rate, then a long position pays (positive cost) and an
    # equal-size short receives (equal-magnitude negative cost)
    sample_funding_rate("ZZZTEST", 0.01)  # 1% for this settlement
    long_cost = funding_cost_usd(
        symbol="ZZZTEST",
        side="long",
        notional_usd=1000,
        entry_time=entry.isoformat(),
        exit_time=exit_.isoformat(),
    )
    short_cost = funding_cost_usd(
        symbol="ZZZTEST",
        side="short",
        notional_usd=1000,
        entry_time=entry.isoformat(),
        exit_time=exit_.isoformat(),
    )
    assert abs(long_cost - 10.0) < 1e-6, long_cost  # 1% of $1000
    assert abs(short_cost + 10.0) < 1e-6, short_cost

    # a window with no settlement inside it -> zero regardless of sampled rate
    tight_entry = datetime(2026, 9, 20, 6, 0, tzinfo=IST).astimezone(timezone.utc)
    tight_exit = datetime(2026, 9, 20, 7, 0, tzinfo=IST).astimezone(timezone.utc)
    assert (
        funding_cost_usd(
            symbol="ZZZTEST",
            side="long",
            notional_usd=1000,
            entry_time=tight_entry.isoformat(),
            exit_time=tight_exit.isoformat(),
        )
        == 0.0
    )

    # missing timestamps -> zero, not an exception
    assert (
        funding_cost_usd(
            symbol="ZZZTEST",
            side="long",
            notional_usd=1000,
            entry_time=None,
            exit_time=None,
        )
        == 0.0
    )

    print("crypto.charges self-check ok")
