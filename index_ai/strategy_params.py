"""Tunable strategy parameters (Supertrend + breakout, Roxx/CPR style)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyParams:
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    breakout_lookback: int = 20
    require_supertrend_align: bool = True
    # If True, long needs Break Res and short needs Break Sup (stricter, chart-like).
    require_breakout_tag: bool = False
    breakout_confidence_boost: float = 0.06
    exit_on_supertrend_flip: bool = True
    # CPR width (% of pivot): narrow → trending, wide → sideways (index intraday).
    cpr_narrow_width_pct: float = 0.35
    cpr_wide_width_pct: float = 0.75
    enable_credit_strategies: bool = True
    credit_wing_strikes: int = 2
    credit_short_strike_steps: int = 2


STRATEGY_PARAMS = StrategyParams()
