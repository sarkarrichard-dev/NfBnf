"""Route CPR regime → buy premium vs hedged credit selling."""

from __future__ import annotations

import os

from index_ai.cpr_regime import CprRegime, analyze_cpr_regime
from index_ai.option_structures import CREDIT_ACTIONS
from index_ai.strategy import StrategySignal, add_indicators, copy_signal, intraday_strategy_signal
from index_ai.strategy_params import get_strategy_params
import pandas as pd


def strategy_style() -> str:
    raw = os.getenv("STRATEGY_STYLE", "AUTO").strip().upper()
    if raw not in {"AUTO", "BUY", "CREDIT"}:
        return "AUTO"
    return raw


def enrich_signal_with_regime(signal: StrategySignal, regime: CprRegime) -> StrategySignal:
    return copy_signal(
        signal,
        pivot=regime.pivot,
        bc=regime.bc,
        tc=regime.tc,
        cpr_width_pct=regime.width_pct,
        cpr_width_class=regime.width_class,
        cpr_regime=regime.day_bias,
        cpr_virgin=regime.virgin_cpr,
        recommended_structure=_structure_for_bias(regime.day_bias),
    )


def _structure_for_bias(day_bias: str) -> str:
    return {
        "SIDEWAYS": "IRON_CONDOR",
        "TRENDING_BULL": "BULL_PUT_SPREAD",
        "TRENDING_BEAR": "BEAR_CALL_SPREAD",
    }.get(day_bias, "")


def _credit_action_for_regime(regime: CprRegime) -> str | None:
    if regime.day_bias == "SIDEWAYS":
        return "SELL_IRON_CONDOR"
    if regime.day_bias == "TRENDING_BULL":
        return "SELL_BULL_PUT_SPREAD"
    if regime.day_bias == "TRENDING_BEAR":
        return "SELL_BEAR_CALL_SPREAD"
    return None


def _credit_confidence(regime: CprRegime) -> float:
    base = get_strategy_params().credit_min_confidence
    if regime.width_class == "WIDE" and regime.day_bias == "SIDEWAYS":
        base = 0.64
    if regime.width_class == "NARROW" and regime.day_bias.startswith("TRENDING"):
        base = 0.62
    if regime.virgin_cpr:
        base = min(0.72, base + 0.04)
    return round(base, 3)


def route_intraday_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    allow_option_selling: bool = True,
) -> tuple[StrategySignal, CprRegime]:
    frame = add_indicators(today) if "ema_fast" not in today.columns else today
    row = frame.iloc[-1]
    regime = analyze_cpr_regime(
        frame,
        previous_day,
        price=float(row["close"]),
        ema_fast=float(row["ema_fast"]),
        ema_slow=float(row["ema_slow"]),
    )
    style = strategy_style()

    params = get_strategy_params()
    if style == "CREDIT" or (style == "AUTO" and allow_option_selling and params.enable_credit_strategies):
        credit_action = _credit_action_for_regime(regime)
        if credit_action and regime.day_bias != "MIXED":
            signal = StrategySignal(
                action=credit_action,
                reason=f"{regime.note} → {credit_action.replace('_', ' ').title()}.",
                confidence=_credit_confidence(regime),
                price=float(row["close"]),
                pivot=regime.pivot,
                bc=regime.bc,
                tc=regime.tc,
                ema_fast=float(row["ema_fast"]),
                ema_slow=float(row["ema_slow"]),
                cpr_width_pct=regime.width_pct,
                cpr_width_class=regime.width_class,
                cpr_regime=regime.day_bias,
                cpr_virgin=regime.virgin_cpr,
                recommended_structure=_structure_for_bias(regime.day_bias),
            )
            return signal, regime

    if style == "CREDIT":
        signal = StrategySignal(
            action="NO_TRADE",
            reason=f"CPR regime {regime.day_bias} — no clear credit setup.",
            confidence=0.0,
            price=float(row["close"]),
            pivot=regime.pivot,
            bc=regime.bc,
            tc=regime.tc,
            ema_fast=float(row["close"]),
            ema_slow=float(row["close"]),
            cpr_regime=regime.day_bias,
        )
        return signal, regime

    buy_signal = intraday_strategy_signal(frame, previous_day)
    return enrich_signal_with_regime(buy_signal, regime), regime
