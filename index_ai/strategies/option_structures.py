"""Hedged option-selling structures from Dhan option chain (margin-friendly)."""

from __future__ import annotations

from typing import Any

from index_ai.strategies.cpr_regime import CprRegime
from index_ai.instruments import IndexInstrument
from index_ai.strategies.strategy import StrategySignal, nearest_strike
from index_ai.strategies.premium_sell import is_premium_sell_action
from index_ai.strategies.credit_spread import attach_credit_risk_metrics
from index_ai.strategies.strategy_params import get_strategy_params


def _chain_rows(chain: dict[str, Any]) -> dict[float, dict[str, Any]]:
    raw = (chain.get("data") or {}).get("oc") or {}
    out: dict[float, dict[str, Any]] = {}
    for key, row in raw.items():
        try:
            out[float(key)] = row
        except ValueError:
            continue
    return out


def _leg(
    rows: dict[float, dict[str, Any]],
    strike: float,
    side: str,
    transaction_type: str,
    instrument: IndexInstrument,
) -> dict[str, Any] | None:
    row = rows.get(strike)
    if row is None:
        nearest = min(rows.keys(), key=lambda k: abs(k - strike), default=None)
        if nearest is None:
            return None
        row = rows[nearest]
        strike = nearest
    leg = row.get(side) or {}
    sid = leg.get("security_id") or leg.get("securityId")
    if sid is None:
        return None
    return {
        "strike": float(strike),
        "option_type": "CALL" if side == "ce" else "PUT",
        "security_id": int(sid),
        "segment": instrument.option_segment,
        "ltp": leg.get("last_price"),
        "transaction_type": transaction_type.upper(),
        "quantity": instrument.lot_size,
    }


def _sum_credit(legs: list[dict[str, Any]]) -> float:
    credit = 0.0
    for leg in legs:
        ltp = float(leg.get("ltp") or 0)
        if leg["transaction_type"] == "SELL":
            credit += ltp
        else:
            credit -= ltp
    return max(0.0, round(credit, 2))


# Hedge (long) leg picked by its *own* premium, not a fixed strike distance —
# a far, cheap hedge so the short's decay isn't masked by the hedge's.
# (lo, hi, max_width_pts): band for the hedge premium, and a hard cap on how far
# the hedge may sit from the short so structural max loss stays bounded even
# though the trailing stop is the real per-trade risk control.
_HEDGE_PREMIUM_BAND: dict[str, tuple[float, float, float]] = {
    "NIFTY": (5.0, 10.0, 300.0),
    "BANKNIFTY": (30.0, 80.0, 700.0),
}


def _row_ltp(
    rows: dict[float, dict[str, Any]], strike: float, side: str
) -> tuple[float | None, float]:
    row = rows.get(strike)
    if row is None:
        nearest = min(rows.keys(), key=lambda k: abs(k - strike), default=None)
        if nearest is None:
            return None, strike
        row, strike = rows[nearest], nearest
    px = (row.get(side) or {}).get("last_price")
    return (float(px) if px is not None else None), strike


def _pick_hedge_strike(
    rows: dict[float, dict[str, Any]],
    short_strike: float,
    short_px: float | None,
    side: str,
    step: int,
    direction: int,
    band: tuple[float, float, float],
) -> float | None:
    """Walk OTM from the short leg; return the hedge strike whose premium sits in
    the band, else the furthest reachable strike within max_width_pts whose
    premium is still <= half the short. None => caller falls back to fixed wings."""
    if not rows or not short_px or short_px <= 0:
        return None
    lo, hi, max_width = band
    cap = 0.5 * float(short_px)
    if cap < lo:
        return None  # short premium too small for a meaningful far hedge
    above_band: float | None = None  # affordable strike just above the band, within the cap
    k = float(short_strike)
    max_steps = max(1, int(max_width // step))
    for _ in range(max_steps):
        k += direction * step
        px, k_res = _row_ltp(rows, k, side)
        if px is None:
            continue
        if px > cap:
            continue  # hedge still richer than half the short — keep walking out
        if lo <= px <= hi:
            return k_res  # in band — done
        if px > hi:
            above_band = k_res  # remember, but keep looking for an in-band strike
            continue
        # px < lo : too cheap. Prefer the last above-band strike; else this one.
        return above_band if above_band is not None else k_res
    return above_band


def build_iron_condor(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    regime: CprRegime,
) -> dict[str, Any]:
    rows = _chain_rows(chain)
    if not rows:
        raise RuntimeError("Empty option chain for iron condor.")
    step = instrument.strike_step
    params = get_strategy_params()
    wings = params.credit_wing_strikes
    atm = nearest_strike(signal.price, instrument)
    sell_call = atm + step * params.credit_short_strike_steps
    buy_call = sell_call + step * wings
    sell_put = atm - step * params.credit_short_strike_steps
    buy_put = sell_put - step * wings

    legs = [
        _leg(rows, buy_call, "ce", "BUY", instrument),
        _leg(rows, buy_put, "pe", "BUY", instrument),
        _leg(rows, sell_call, "ce", "SELL", instrument),
        _leg(rows, sell_put, "pe", "SELL", instrument),
    ]
    if any(x is None for x in legs):
        raise RuntimeError("Could not resolve all iron condor legs on chain.")
    legs_typed: list[dict[str, Any]] = [x for x in legs if x is not None]
    short = next(
        leg for leg in legs_typed if str(leg.get("transaction_type") or "").upper() == "SELL"
    )
    return attach_credit_risk_metrics(
        {
            "instrument": instrument.key,
            "structure": "IRON_CONDOR",
            "transaction_type": "SELL",
            "option_type": "SPREAD",
            "strike": short["strike"],
            "security_id": short["security_id"],
            "segment": instrument.option_segment,
            "quantity": instrument.lot_size,
            "ltp": _sum_credit(legs_typed),
            "net_credit_points": _sum_credit(legs_typed),
            "legs": legs_typed,
            "cpr_regime": regime.day_bias,
            "hedge_note": f"Hedged iron condor — wings {wings} strikes each side (defined risk).",
        },
        instrument,
    )


def build_bull_put_spread(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    regime: CprRegime,
) -> dict[str, Any]:
    rows = _chain_rows(chain)
    step = instrument.strike_step
    params = get_strategy_params()
    wings = params.credit_wing_strikes
    atm = nearest_strike(signal.price, instrument)
    sell_put = atm - step * params.credit_short_strike_steps
    short_px, sell_put = _row_ltp(rows, sell_put, "pe")
    band = _HEDGE_PREMIUM_BAND.get(instrument.key)
    hedge_k = _pick_hedge_strike(rows, sell_put, short_px, "pe", step, -1, band) if band else None
    buy_put = hedge_k if hedge_k is not None else sell_put - step * wings
    legs_raw = [
        _leg(rows, buy_put, "pe", "BUY", instrument),
        _leg(rows, sell_put, "pe", "SELL", instrument),
    ]
    if any(x is None for x in legs_raw):
        raise RuntimeError("Could not resolve bull put spread legs.")
    legs = [x for x in legs_raw if x is not None]
    short = next(leg for leg in legs if str(leg.get("transaction_type") or "").upper() == "SELL")
    return attach_credit_risk_metrics(
        {
            "instrument": instrument.key,
            "structure": "BULL_PUT_SPREAD",
            "transaction_type": "SELL",
            "option_type": "SPREAD",
            "strike": short["strike"],
            "security_id": short["security_id"],
            "segment": instrument.option_segment,
            "quantity": instrument.lot_size,
            "ltp": _sum_credit(legs),
            "net_credit_points": _sum_credit(legs),
            "legs": legs,
            "cpr_regime": regime.day_bias,
            "hedge_note": "Bull put credit spread — long put wing caps downside.",
        },
        instrument,
    )


def build_bear_call_spread(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    regime: CprRegime,
) -> dict[str, Any]:
    rows = _chain_rows(chain)
    step = instrument.strike_step
    params = get_strategy_params()
    wings = params.credit_wing_strikes
    atm = nearest_strike(signal.price, instrument)
    sell_call = atm + step * params.credit_short_strike_steps
    short_px, sell_call = _row_ltp(rows, sell_call, "ce")
    band = _HEDGE_PREMIUM_BAND.get(instrument.key)
    hedge_k = _pick_hedge_strike(rows, sell_call, short_px, "ce", step, +1, band) if band else None
    buy_call = hedge_k if hedge_k is not None else sell_call + step * wings
    legs_raw = [
        _leg(rows, buy_call, "ce", "BUY", instrument),
        _leg(rows, sell_call, "ce", "SELL", instrument),
    ]
    if any(x is None for x in legs_raw):
        raise RuntimeError("Could not resolve bear call spread legs.")
    legs = [x for x in legs_raw if x is not None]
    short = next(leg for leg in legs if str(leg.get("transaction_type") or "").upper() == "SELL")
    return attach_credit_risk_metrics(
        {
            "instrument": instrument.key,
            "structure": "BEAR_CALL_SPREAD",
            "transaction_type": "SELL",
            "option_type": "SPREAD",
            "strike": short["strike"],
            "security_id": short["security_id"],
            "segment": instrument.option_segment,
            "quantity": instrument.lot_size,
            "ltp": _sum_credit(legs),
            "net_credit_points": _sum_credit(legs),
            "legs": legs,
            "cpr_regime": regime.day_bias,
            "hedge_note": "Bear call credit spread — long call wing caps upside risk.",
        },
        instrument,
    )


def build_atm_short_option(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
) -> dict[str, Any]:
    """Sell nearest ATM call or put (Apex Pivot-Trend / naked premium sell)."""
    action = str(signal.action or "").upper()
    if not is_premium_sell_action(action):
        raise ValueError(f"Not a premium sell action: {action}")
    side = "pe" if action == "SELL_ATM_PUT" else "ce"
    opt_type = "PUT" if side == "pe" else "CALL"
    rows = _chain_rows(chain)
    if not rows:
        raise RuntimeError("Empty option chain for ATM sell.")
    atm = nearest_strike(signal.price, instrument)
    leg = _leg(rows, atm, side, "SELL", instrument)
    if leg is None:
        raise RuntimeError(f"Could not resolve ATM {opt_type} on chain.")
    return {
        "instrument": instrument.key,
        "structure": f"ATM_SHORT_{opt_type}",
        "transaction_type": "SELL",
        "option_type": opt_type,
        "strike": leg["strike"],
        "security_id": leg["security_id"],
        "segment": instrument.option_segment,
        "quantity": instrument.lot_size,
        "ltp": leg.get("ltp"),
        "legs": [leg],
        "hedge_note": "Short ATM premium (Apex Pivot-Trend). Defined risk only if hedged spread mode is on.",
    }


def build_credit_structure(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    regime: CprRegime,
) -> dict[str, Any]:
    action = signal.action
    if action == "SELL_IRON_CONDOR":
        return build_iron_condor(chain, signal, instrument, regime)
    if action == "SELL_BULL_PUT_SPREAD":
        return build_bull_put_spread(chain, signal, instrument, regime)
    if action == "SELL_BEAR_CALL_SPREAD":
        return build_bear_call_spread(chain, signal, instrument, regime)
    raise ValueError(f"Not a credit structure action: {action}")
