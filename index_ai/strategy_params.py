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


STRATEGY_PARAMS = StrategyParams()
