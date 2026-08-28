"""
Realistic round-trip cost model for NSE / BSE index-option intraday trades.

Two independent pieces:

  * **Statutory + broker charges** — brokerage, STT, exchange transaction charges,
    SEBI turnover fee, GST, stamp duty. Deterministic functions of premium
    turnover and side. Rates are the post-1-Oct-2024 Indian F&O schedule and are
    all overridable from ``.env`` (see ``ChargeRates``).
  * **Slippage** — half the bid/ask spread, paid on entry and exit, per leg.
    Index-option spreads are wide enough intraday that this usually dwarfs the
    statutory charges, so it is modelled separately and conservatively.

Used by the backtest (so replay P&L is net of real friction) and by the live
entry gate (so the system refuses trades that cannot clear their own costs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

Side = Literal["BUY", "SELL"]

# Per-index default half-spread, in *points of option premium*, paid each side.
# Deliberately conservative — calibrate against the paper journal once live.
_DEFAULT_HALF_SPREAD_POINTS: dict[str, float] = {
    "NIFTY": 0.75,
    "BANKNIFTY": 2.0,
    "SENSEX": 3.0,
    "FINNIFTY": 1.0,
    "MIDCPNIFTY": 0.75,
}
_FALLBACK_HALF_SPREAD_POINTS = 1.5


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ChargeRates:
    """All rates are fractions of premium turnover unless noted."""

    brokerage_per_order_rupees: float = 20.0     # Dhan F&O flat
    brokerage_pct: float = 0.0003                # min(flat, pct*turnover)
    stt_sell_pct: float = 0.001                  # 0.10% on option SELL premium
    exch_txn_pct_nse: float = 0.0003503          # NSE options, per side
    exch_txn_pct_bse: float = 0.000325           # BSE options, per side
    sebi_pct: float = 0.000001                   # Rs 10 / crore, per side
    gst_pct: float = 0.18                        # on brokerage + exch txn + sebi
    stamp_buy_pct: float = 0.00003               # 0.003% on BUY side only

    @staticmethod
    @lru_cache(maxsize=1)
    def load() -> "ChargeRates":
        return ChargeRates(
            brokerage_per_order_rupees=_f("CHARGE_BROKERAGE_PER_ORDER", 20.0),
            brokerage_pct=_f("CHARGE_BROKERAGE_PCT", 0.0003),
            stt_sell_pct=_f("CHARGE_STT_SELL_PCT", 0.001),
            exch_txn_pct_nse=_f("CHARGE_EXCH_TXN_PCT_NSE", 0.0003503),
            exch_txn_pct_bse=_f("CHARGE_EXCH_TXN_PCT_BSE", 0.000325),
            sebi_pct=_f("CHARGE_SEBI_PCT", 0.000001),
            gst_pct=_f("CHARGE_GST_PCT", 0.18),
            stamp_buy_pct=_f("CHARGE_STAMP_BUY_PCT", 0.00003),
        )


def leg_charge_rupees(
    premium: float,
    qty: int,
    side: Side,
    *,
    exchange: str = "NSE",
    rates: ChargeRates | None = None,
) -> float:
    """Statutory + broker cost for a single executed option leg on one side."""
    r = rates or ChargeRates.load()
    turnover = max(0.0, float(premium)) * max(0, int(qty))
    if turnover <= 0:
        return 0.0

    brokerage = min(r.brokerage_per_order_rupees, r.brokerage_pct * turnover)
    exch_pct = r.exch_txn_pct_bse if str(exchange).upper() in {"BSE", "BFO"} else r.exch_txn_pct_nse
    exch_txn = exch_pct * turnover
    sebi = r.sebi_pct * turnover
    stt = r.stt_sell_pct * turnover if side == "SELL" else 0.0
    stamp = r.stamp_buy_pct * turnover if side == "BUY" else 0.0
    gst = r.gst_pct * (brokerage + exch_txn + sebi)
    return round(brokerage + exch_txn + sebi + stt + stamp + gst, 2)


def _structural_legs(option: dict[str, Any]) -> list[dict[str, Any]]:
    legs = list(option.get("legs") or [])
    if legs:
        return legs
    return [
        {
            "ltp": option.get("ltp", option.get("last_price", 0)),
            "transaction_type": option.get("transaction_type", "BUY"),
        }
    ]


def round_trip_charges_rupees(
    option: dict[str, Any],
    qty: int,
    *,
    exchange: str = "NSE",
    exit_premium_factor: float = 1.0,
    rates: ChargeRates | None = None,
) -> float:
    """Open + close statutory/broker cost for every leg of an option structure.

    ``exit_premium_factor`` scales the assumed exit premium relative to entry
    (1.0 = flat). Charges are close to linear in premium, so this is a minor knob.
    """
    total = 0.0
    for leg in _structural_legs(option):
        entry_px = abs(float(leg.get("ltp") or leg.get("last_price") or 0.0))
        exit_px = entry_px * max(0.0, float(exit_premium_factor))
        open_side: Side = "SELL" if str(leg.get("transaction_type", "BUY")).upper() == "SELL" else "BUY"
        close_side: Side = "BUY" if open_side == "SELL" else "SELL"
        total += leg_charge_rupees(entry_px, qty, open_side, exchange=exchange, rates=rates)
        total += leg_charge_rupees(exit_px, qty, close_side, exchange=exchange, rates=rates)
    return round(total, 2)


_FUT_HALF_SPREAD_POINTS: dict[str, float] = {
    "NIFTY": 0.5, "BANKNIFTY": 1.5, "SENSEX": 2.0, "FINNIFTY": 0.75, "MIDCPNIFTY": 0.5
}


def futures_round_trip_rupees(
    price: float, lot: int, instrument_key: str = "NIFTY", *, rates: ChargeRates | None = None
) -> float:
    """Open + close statutory/broker cost for one index-futures lot (no slippage).

    STT on futures is 0.02% on the SELL side only (post 1-Oct-2024); exchange txn
    ~0.0019%/side; stamp 0.002% on BUY. Much lighter than a multi-leg option
    spread relative to the rupee move a futures point represents.
    """
    r = rates or ChargeRates.load()
    turnover = max(0.0, float(price)) * max(0, int(lot))
    if turnover <= 0:
        return 0.0
    exch = float(os.getenv("CHARGE_FUT_EXCH_TXN_PCT", "0.0000190"))
    stt_sell = float(os.getenv("CHARGE_FUT_STT_SELL_PCT", "0.0002"))
    stamp_buy = float(os.getenv("CHARGE_FUT_STAMP_BUY_PCT", "0.00002"))
    brokerage = 2 * min(r.brokerage_per_order_rupees, r.brokerage_pct * turnover)
    exch_txn = 2 * exch * turnover
    sebi = 2 * r.sebi_pct * turnover
    stt = stt_sell * turnover
    stamp = stamp_buy * turnover
    gst = r.gst_pct * (brokerage + exch_txn + sebi)
    return round(brokerage + exch_txn + sebi + stt + stamp + gst, 2)


def futures_slippage_rupees(lot: int, instrument_key: str) -> float:
    key = str(instrument_key or "").upper()
    env = os.getenv(f"SLIPPAGE_FUT_HALF_SPREAD_POINTS_{key}")
    hs = _FUT_HALF_SPREAD_POINTS.get(key, 1.0)
    if env:
        try:
            hs = max(0.0, float(env))
        except ValueError:
            pass
    return round(hs * max(0, int(lot)) * 2, 2)  # half-spread each side


def half_spread_points(instrument_key: str) -> float:
    key = str(instrument_key or "").upper()
    env = os.getenv(f"SLIPPAGE_HALF_SPREAD_POINTS_{key}")
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    return _DEFAULT_HALF_SPREAD_POINTS.get(key, _FALLBACK_HALF_SPREAD_POINTS)


def round_trip_slippage_rupees(
    option: dict[str, Any],
    qty: int,
    instrument_key: str,
) -> float:
    """Half-spread paid on entry and on exit, for every leg."""
    legs = _structural_legs(option)
    hs = half_spread_points(instrument_key)
    return round(hs * max(0, int(qty)) * len(legs) * 2, 2)


@dataclass(frozen=True)
class TradeCost:
    charges_rupees: float
    slippage_rupees: float
    legs: int

    @property
    def total_rupees(self) -> float:
        return round(self.charges_rupees + self.slippage_rupees, 2)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "charges_rupees": self.charges_rupees,
            "slippage_rupees": self.slippage_rupees,
            "total_rupees": self.total_rupees,
            "legs": self.legs,
        }


def estimate_trade_cost(
    option: dict[str, Any],
    qty: int,
    instrument_key: str,
    *,
    exit_premium_factor: float = 1.0,
) -> TradeCost:
    """Full round-trip cost (charges + slippage) for a proposed option trade."""
    exchange = "BSE" if str(instrument_key or "").upper() == "SENSEX" else "NSE"
    charges = round_trip_charges_rupees(
        option, qty, exchange=exchange, exit_premium_factor=exit_premium_factor
    )
    slippage = round_trip_slippage_rupees(option, qty, instrument_key)
    return TradeCost(charges, slippage, len(_structural_legs(option)))


def reload_charge_rates() -> ChargeRates:
    ChargeRates.load.cache_clear()
    return ChargeRates.load()
