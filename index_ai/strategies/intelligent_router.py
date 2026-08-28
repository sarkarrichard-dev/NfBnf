"""Per-scan EMA/CPR credit selection for STRATEGY_STYLE=AUTO (Apex removed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.strategy_mode import pick_auto_credit
from index_ai.strategies.strategy_params import StrategyParams


@dataclass(frozen=True)
class AutoEngineChoice:
    """Which subsystem drives this AUTO scan."""

    engine: str  # ema_credit | wait
    action: str | None
    reason: str
    strategy_mode: str
    apex_confidence: float = 0.0


def choose_auto_engine(
    frame: pd.DataFrame,
    previous_day: pd.DataFrame,
    regime: CprRegime,
    cross: dict[str, Any],
    *,
    params: StrategyParams,
    close: float,
) -> AutoEngineChoice:
    """CPR + 1m EMA cross/alignment + volume — no Apex pivot routing."""
    _ = previous_day, close
    credit_action, credit_reason, credit_mode = pick_auto_credit(
        regime,
        cross,
        ema_fast=params.ema_fast_period,
        ema_slow=params.ema_slow_period,
        frame=frame,
    )
    if credit_action:
        return AutoEngineChoice(
            "ema_credit",
            credit_action,
            f"AUTO: {credit_reason}",
            credit_mode,
        )
    return AutoEngineChoice("wait", None, credit_reason, credit_mode)
