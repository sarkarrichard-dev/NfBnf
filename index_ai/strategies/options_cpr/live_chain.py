"""
Real option premiums from Dhan's live option chain, for forward paper-trading.

The backtest uses a Black-Scholes proxy because there is no historical option
chain. Forward, there is: ``/optionchain`` returns per-strike CE/PE last price,
IV, greeks, OI and (when available) top bid/ask. This module resolves a strike
to a real quote so the paper lane trades on actual premiums and real Dhan
charges instead of the proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from index_ai.instruments import IndexInstrument
from index_ai.market_clock import now_ist
from index_ai.options_expiry import parse_expiry_date, pick_nearest_expiry

_MIN_DTE_DAYS = 1  # roll to the next expiry inside the last day (gamma / pin risk)


@dataclass(frozen=True)
class LegQuote:
    strike: float
    is_call: bool
    ltp: float
    iv: float | None
    bid: float | None
    ask: float | None
    security_id: int | None
    delta: float | None

    def fill(self, side: str) -> float:
        """Marketable fill: buy at ask, sell at bid, LTP if the book is absent."""
        if side.upper() == "BUY" and self.ask and self.ask > 0:
            return float(self.ask)
        if side.upper() == "SELL" and self.bid and self.bid > 0:
            return float(self.bid)
        return float(self.ltp)


class ChainBook:
    """Parsed live chain for one instrument+expiry. Build once per scan."""

    def __init__(self, expiry: str, rows: dict[float, dict[str, Any]]):
        self.expiry = expiry
        self._rows = rows

    @classmethod
    def fetch(cls, client: Any, inst: IndexInstrument, *, now: datetime | None = None) -> "ChainBook | None":
        try:
            expiries = client.expiry_list(inst)
            expiry = pick_nearest_expiry(expiries, now=now or now_ist())
            if not expiry:
                return None
            d = parse_expiry_date(expiry)
            ref = (now or now_ist()).date()
            if d is not None and (d - ref).days < _MIN_DTE_DAYS and len(expiries) > 1:
                nxt = pick_nearest_expiry(
                    [e for e in expiries if parse_expiry_date(e) and parse_expiry_date(e) > d],
                    now=now or now_ist(),
                )
                expiry = nxt or expiry
            chain = client.option_chain(inst, expiry)
        except Exception:
            return None
        raw = (chain.get("data") or {}).get("oc") or {}
        rows: dict[float, dict[str, Any]] = {}
        for k, v in raw.items():
            try:
                rows[float(k)] = v
            except (TypeError, ValueError):
                continue
        return cls(expiry, rows) if rows else None

    def _leg_raw(self, strike: float, is_call: bool) -> tuple[float, dict[str, Any]] | None:
        row = self._rows.get(strike)
        k = strike
        if row is None and self._rows:
            k = min(self._rows, key=lambda x: abs(x - strike))
            row = self._rows[k]
        if not row:
            return None
        leg = row.get("ce" if is_call else "pe") or {}
        return (k, leg) if leg else None

    def quote(self, strike: float, is_call: bool) -> LegQuote | None:
        hit = self._leg_raw(strike, is_call)
        if hit is None:
            return None
        k, leg = hit
        ltp = leg.get("last_price") or leg.get("ltp")
        if ltp is None or float(ltp) <= 0:
            return None
        greeks = leg.get("greeks") or {}
        return LegQuote(
            strike=float(k),
            is_call=is_call,
            ltp=float(ltp),
            iv=_f(leg.get("implied_volatility")),
            bid=_f(leg.get("top_bid_price") or leg.get("bid_price")),
            ask=_f(leg.get("top_ask_price") or leg.get("ask_price")),
            security_id=_i(leg.get("security_id") or leg.get("securityId")),
            delta=_f(greeks.get("delta")),
        )

    def strike_for_delta(self, spot: float, step: int, is_call: bool, target_delta: float) -> float:
        """Nearest strike whose |delta| is closest to target, using the chain's own
        greeks where present, else moneyness."""
        atm = round(spot / step) * float(step)
        best, best_err, seen = atm, 9.9, set()
        for kk in range(-12, 13):
            q = self.quote(atm + kk * step, is_call)
            if q is None or q.strike in seen:
                continue
            seen.add(q.strike)
            if is_call and q.strike < atm - step:
                continue
            if not is_call and q.strike > atm + step:
                continue
            d = abs(q.delta) if q.delta is not None else _moneyness_delta(spot, q.strike, is_call)
            if abs(d - target_delta) < best_err:
                best, best_err = q.strike, abs(d - target_delta)
        return best


def _f(x: Any) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _i(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _moneyness_delta(spot: float, strike: float, is_call: bool) -> float:
    """Very rough |delta| from moneyness when the chain has no greeks."""
    m = (spot - strike) / max(spot, 1.0)
    base = 0.5 + 8.0 * m if is_call else 0.5 - 8.0 * m
    return max(0.02, min(0.98, base))


if __name__ == "__main__":  # ponytail self-check
    rows = {
        24000.0: {"ce": {"last_price": 120.0, "top_bid_price": 119.0, "top_ask_price": 121.5,
                         "security_id": 111, "implied_volatility": 12.4,
                         "greeks": {"delta": 0.51}},
                  "pe": {"last_price": 118.0, "top_bid_price": 117.0, "top_ask_price": 119.0,
                         "security_id": 112, "greeks": {"delta": -0.49}}},
        23000.0: {"pe": {"last_price": 8.0, "top_bid_price": 7.0, "top_ask_price": 9.0,
                         "security_id": 113, "greeks": {"delta": -0.06}}},
    }
    book = ChainBook("2026-09-02", rows)
    q = book.quote(24000.0, is_call=True)
    assert q and q.ltp == 120.0 and q.fill("BUY") == 121.5 and q.fill("SELL") == 119.0
    assert book.quote(23990.0, is_call=True).strike == 24000.0  # nearest-strike fallback
    wing = book.quote(23000.0, is_call=False)
    assert wing and wing.security_id == 113
    k = book.strike_for_delta(24000.0, 1000, is_call=False, target_delta=0.06)
    assert k == 23000.0, k
    print("live_chain.py self-check ok")
