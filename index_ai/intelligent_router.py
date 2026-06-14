"""
Per-scan engine selection for STRATEGY_STYLE=AUTO.

Picks Apex Pivot-Trend on R1/S1 breakouts with Supertrend confirm; otherwise
EMA/CPR credit (pick_auto_credit); buy rules remain the fallback in strategy_router.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from index_ai.apex_pivot_trend import apex_pivot_trend_signal, map_apex_to_hedged_credit
from index_ai.cpr_regime import CprRegime
from index_ai.pivot_points import classic_pivot_levels
from index_ai.premium_sell import PREMIUM_SELL_ACTIONS
from index_ai.strategy_mode import pick_auto_credit
from index_ai.strategy_params import StrategyParams


@dataclass(frozen=True)
class AutoEngineChoice:
    """Which subsystem drives this AUTO scan."""

    engine: str  # apex | ema_credit | wait
    action: str | None
    reason: str
    strategy_mode: str
    apex_confidence: float = 0.0


def _outside_pivot_range(close: float, previous_day: pd.DataFrame) -> bool:
    _, r1, s1, _ = classic_pivot_levels(previous_day)
    return close > r1 or close < s1


def choose_auto_engine(
    frame: pd.DataFrame,
    previous_day: pd.DataFrame,
    regime: CprRegime,
    cross: dict[str, Any],
    *,
    params: StrategyParams,
    close: float,
) -> AutoEngineChoice:
    """
    Decide Apex vs EMA/CPR credit for one AUTO scan.

    Priority:
    1. Confirmed Apex breakout (outside R1/S1 + ST aligned) when AUTO_INCLUDE_APEX.
    2. Intelligent EMA/CPR credit (pick_auto_credit).
    3. Wait (may still fall through to buy rules in strategy_router).
    """
    credit_action, credit_reason, credit_mode = pick_auto_credit(
        regime,
        cross,
        ema_fast=params.ema_fast_period,
        ema_slow=params.ema_slow_period,
    )

    if not params.auto_include_apex:
        if credit_action:
            return AutoEngineChoice(
                "ema_credit", credit_action, credit_reason, credit_mode
            )
        return AutoEngineChoice("wait", None, credit_reason, credit_mode)

    apex = apex_pivot_trend_signal(frame, previous_day)
    apex_action = str(apex.action or "").upper()
    apex_ready = apex_action in PREMIUM_SELL_ACTIONS
    outside = _outside_pivot_range(close, previous_day)

    if apex_ready and outside:
        action = apex_action
        mode = "apex"
        reason = f"AUTO [Apex]: {apex.reason}"
        if params.apex_use_hedged_spreads:
            hedged = map_apex_to_hedged_credit(action)
            if hedged:
                action = hedged
                mode = "apex_hedged"
                reason = f"AUTO [Apex→hedged]: {apex.reason}"
        return AutoEngineChoice(
            "apex",
            action,
            reason,
            mode,
            apex_confidence=float(apex.confidence),
        )

    if credit_action:
        prefix = "AUTO"
        if outside and apex.strategy_mode == "apex_wait":
            prefix = "AUTO [EMA/CPR over Apex wait]"
        return AutoEngineChoice(
            "ema_credit",
            credit_action,
            f"{prefix}: {credit_reason}",
            credit_mode,
        )

    if outside and apex.strategy_mode == "apex_wait":
        return AutoEngineChoice(
            "wait",
            None,
            f"AUTO: {apex.reason} No EMA/CPR credit this scan.",
            "apex_wait",
        )

    return AutoEngineChoice("wait", None, credit_reason, credit_mode)
