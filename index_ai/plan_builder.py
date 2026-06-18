"""Build option legs + execution plan for a strategy signal."""

from __future__ import annotations

from typing import Any

from index_ai.capital_required import compute_capital_required
from index_ai.credit_spread import CREDIT_ACTIONS
from index_ai.executor import build_execution_plan
from index_ai.instruments import IndexInstrument
from index_ai.option_structures import build_atm_short_option, build_credit_structure
from index_ai.options_oi import apply_oi_to_signal, choose_option_from_chain_with_oi
from index_ai.premium_sell import PREMIUM_SELL_ACTIONS
from index_ai.risk_policy import HARDCODED_RISK
from index_ai.strategy import StrategySignal, copy_signal
from index_ai.strategy_params import get_strategy_params
from index_ai.trade_lots import stamp_option_quantities
from index_ai.config import AppSettings


def attach_option_to_signal(
    signal: StrategySignal,
    *,
    chain: dict[str, Any],
    oi,
    instrument: IndexInstrument,
    cpr_regime,
    expiry: str | None,
) -> tuple[StrategySignal, dict[str, Any] | None]:
    """Resolve chain legs for buy / credit / naked sell signals."""
    if signal.action == "NO_TRADE" or not chain or oi is None:
        return signal, None

    sp = get_strategy_params()
    option: dict[str, Any] | None = None
    work = signal

    if signal.action in PREMIUM_SELL_ACTIONS:
        try:
            if sp.apex_use_hedged_spreads:
                from index_ai.apex_pivot_trend import map_apex_to_hedged_credit

                hedged = map_apex_to_hedged_credit(signal.action)
                if hedged:
                    work = copy_signal(
                        signal,
                        action=hedged,
                        reason=f"{signal.reason} (hedged spread for defined risk).",
                    )
                    option = build_credit_structure(chain, work, instrument, cpr_regime)
                else:
                    option = None
            else:
                option = build_atm_short_option(chain, signal, instrument)
        except Exception as exc:
            return (
                copy_signal(
                    signal,
                    action="NO_TRADE",
                    reason=f"Premium sell option build failed: {exc}",
                    confidence=0.0,
                ),
                None,
            )
    elif signal.action in CREDIT_ACTIONS:
        try:
            option = build_credit_structure(chain, signal, instrument, cpr_regime)
        except Exception as exc:
            return (
                copy_signal(
                    signal,
                    action="NO_TRADE",
                    reason=f"Credit structure build failed: {exc}",
                    confidence=0.0,
                ),
                None,
            )
    else:
        work = apply_oi_to_signal(signal, oi)
        if work.confidence < HARDCODED_RISK.min_confidence:
            return (
                copy_signal(
                    work,
                    action="NO_TRADE",
                    reason=(
                        f"OI-filtered: confidence {work.confidence} below gate after OI adjustment."
                    ),
                    confidence=0.0,
                ),
                None,
            )
        option = choose_option_from_chain_with_oi(
            chain,
            work,
            instrument,
            oi,
            transaction_type=HARDCODED_RISK.default_option_transaction,
        )
        signal = work

    if option and expiry:
        option = {
            **option,
            "expiry": expiry,
            "chain_pcr": oi.pcr,
            "chain_bias": oi.bias,
            "oi_note": oi.note,
        }
    if option:
        option = stamp_option_quantities(option, instrument)
    return signal, option


def build_opportunity(
    *,
    app_settings: AppSettings,
    instrument: IndexInstrument,
    signal: StrategySignal,
    chain: dict[str, Any] | None,
    oi,
    expiry: str | None,
    cpr_regime,
    lane: str,
) -> dict[str, Any] | None:
    if signal.action == "NO_TRADE" or not chain or oi is None:
        return None

    resolved, option = attach_option_to_signal(
        signal,
        chain=chain,
        oi=oi,
        instrument=instrument,
        cpr_regime=cpr_regime,
        expiry=expiry,
    )
    if resolved.action == "NO_TRADE":
        return {
            "lane": lane,
            "signal": resolved.to_dict(),
            "option": None,
            "plan": build_execution_plan(
                app_settings=app_settings,
                instrument=instrument,
                signal=resolved,
                option=None,
            ).__dict__,
            "capital_required": None,
        }

    capital = compute_capital_required(option, instrument) if option else None
    plan = build_execution_plan(
        app_settings=app_settings,
        instrument=instrument,
        signal=resolved,
        option=option,
    )
    return {
        "lane": lane,
        "signal": resolved.to_dict(),
        "option": option,
        "plan": plan.__dict__,
        "capital_required": capital,
    }
