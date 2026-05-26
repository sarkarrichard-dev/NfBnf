"""Hedged option-selling structures from Dhan option chain (margin-friendly)."""

from __future__ import annotations

from typing import Any

from index_ai.cpr_regime import CprRegime
from index_ai.instruments import IndexInstrument
from index_ai.strategy import StrategySignal, nearest_strike
from index_ai.credit_spread import CREDIT_ACTIONS, attach_credit_risk_metrics
from index_ai.strategy_params import get_strategy_params


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
    sid = leg.get("security_id")
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
        _leg(rows, sell_call, "ce", "SELL", instrument),
        _leg(rows, buy_call, "ce", "BUY", instrument),
        _leg(rows, sell_put, "pe", "SELL", instrument),
        _leg(rows, buy_put, "pe", "BUY", instrument),
    ]
    if any(x is None for x in legs):
        raise RuntimeError("Could not resolve all iron condor legs on chain.")
    legs_typed: list[dict[str, Any]] = [x for x in legs if x is not None]
    short = legs_typed[0]
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
    buy_put = sell_put - step * wings
    legs_raw = [
        _leg(rows, sell_put, "pe", "SELL", instrument),
        _leg(rows, buy_put, "pe", "BUY", instrument),
    ]
    if any(x is None for x in legs_raw):
        raise RuntimeError("Could not resolve bull put spread legs.")
    legs = [x for x in legs_raw if x is not None]
    short = legs[0]
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
    buy_call = sell_call + step * wings
    legs_raw = [
        _leg(rows, sell_call, "ce", "SELL", instrument),
        _leg(rows, buy_call, "ce", "BUY", instrument),
    ]
    if any(x is None for x in legs_raw):
        raise RuntimeError("Could not resolve bear call spread legs.")
    legs = [x for x in legs_raw if x is not None]
    short = legs[0]
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
