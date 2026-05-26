"""Tunable strategy parameters — load from .env (restart server after edits)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from index_ai.config import ENV_PATH


@dataclass(frozen=True)
class StrategyParams:
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    breakout_lookback: int = 20
    require_supertrend_align: bool = True
    require_breakout_tag: bool = False
    breakout_confidence_boost: float = 0.06
    exit_on_supertrend_flip: bool = True
    cpr_narrow_width_pct: float = 0.35
    cpr_wide_width_pct: float = 0.75
    enable_credit_strategies: bool = True
    credit_wing_strikes: int = 2
    credit_short_strike_steps: int = 2
    credit_min_confidence: float = 0.58
    credit_profit_target_pct: float = 0.50
    credit_stop_loss_pct: float = 0.60


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_strategy_params() -> StrategyParams:
    load_dotenv(ENV_PATH, override=True)
    return StrategyParams(
        supertrend_period=_int("SUPERTREND_PERIOD", 10),
        supertrend_multiplier=_float("SUPERTREND_MULTIPLIER", 3.0),
        breakout_lookback=_int("BREAKOUT_LOOKBACK", 20),
        require_supertrend_align=_bool("REQUIRE_SUPERTREND_ALIGN", True),
        require_breakout_tag=_bool("REQUIRE_BREAKOUT_TAG", False),
        breakout_confidence_boost=_float("BREAKOUT_CONFIDENCE_BOOST", 0.06),
        exit_on_supertrend_flip=_bool("EXIT_ON_SUPERTREND_FLIP", True),
        cpr_narrow_width_pct=_float("CPR_NARROW_WIDTH_PCT", 0.35),
        cpr_wide_width_pct=_float("CPR_WIDE_WIDTH_PCT", 0.75),
        enable_credit_strategies=_bool("ENABLE_CREDIT_STRATEGIES", True),
        credit_wing_strikes=_int("CREDIT_WING_STRIKES", 2),
        credit_short_strike_steps=_int("CREDIT_SHORT_STRIKE_STEPS", 2),
        credit_min_confidence=_float("CPR_CREDIT_MIN_CONFIDENCE", 0.58),
        credit_profit_target_pct=_float("CREDIT_PROFIT_TARGET_PCT", 0.50),
        credit_stop_loss_pct=_float("CREDIT_STOP_LOSS_PCT", 0.60),
    )


def strategy_tuning_summary() -> dict[str, object]:
    """Active tuning values for dashboard / API (edit .env, then restart)."""
    from index_ai.strategy_router import strategy_style

    p = get_strategy_params()
    style = strategy_style()
    return {
        "strategy_style": style,
        "strategy_style_note": {
            "AUTO": "Credit when CPR regime is clear; else buy call/put with Supertrend.",
            "CREDIT": "Only hedged iron condor / credit spreads.",
            "BUY": "Only long premium (calls/puts).",
        }.get(style, ""),
        "enable_credit_strategies": p.enable_credit_strategies,
        "cpr_narrow_width_pct": p.cpr_narrow_width_pct,
        "cpr_wide_width_pct": p.cpr_wide_width_pct,
        "cpr_width_note": (
            f"Narrow ≤ {p.cpr_narrow_width_pct}% of pivot → trending; "
            f"wide ≥ {p.cpr_wide_width_pct}% → sideways."
        ),
        "credit_wing_strikes": p.credit_wing_strikes,
        "credit_short_strike_steps": p.credit_short_strike_steps,
        "credit_min_confidence": p.credit_min_confidence,
        "credit_profit_target_pct": p.credit_profit_target_pct,
        "credit_stop_loss_pct": p.credit_stop_loss_pct,
        "require_supertrend_align": p.require_supertrend_align,
        "require_breakout_tag": p.require_breakout_tag,
        "supertrend_period": p.supertrend_period,
        "supertrend_multiplier": p.supertrend_multiplier,
        "env_keys": [
            "STRATEGY_STYLE",
            "ENABLE_CREDIT_STRATEGIES",
            "CPR_NARROW_WIDTH_PCT",
            "CPR_WIDE_WIDTH_PCT",
            "CPR_CREDIT_MIN_CONFIDENCE",
            "CREDIT_WING_STRIKES",
            "CREDIT_SHORT_STRIKE_STEPS",
            "CREDIT_PROFIT_TARGET_PCT",
            "CREDIT_STOP_LOSS_PCT",
            "REQUIRE_SUPERTREND_ALIGN",
            "REQUIRE_BREAKOUT_TAG",
            "SUPERTREND_PERIOD",
            "SUPERTREND_MULTIPLIER",
        ],
        "presets": {
            "more_sideways_credit": {
                "CPR_WIDE_WIDTH_PCT": "0.65",
                "CPR_NARROW_WIDTH_PCT": "0.30",
                "STRATEGY_STYLE": "AUTO",
            },
            "stricter_trending": {
                "CPR_NARROW_WIDTH_PCT": "0.40",
                "REQUIRE_SUPERTREND_ALIGN": "true",
                "REQUIRE_BREAKOUT_TAG": "true",
            },
            "buy_only": {
                "STRATEGY_STYLE": "BUY",
            },
        },
    }


def reload_strategy_params() -> StrategyParams:
    get_strategy_params.cache_clear()
    return get_strategy_params()
