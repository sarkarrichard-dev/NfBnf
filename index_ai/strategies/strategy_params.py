"""Tunable strategy parameters — load from .env (restart server after edits)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from index_ai.config import ENV_PATH


@dataclass(frozen=True)
class StrategyParams:
    ema_fast_period: int = 8
    ema_slow_period: int = 20
    auto_intelligent_routing: bool = True
    auto_include_apex: bool = False
    auto_trend_buy_first: bool = True
    auto_credit_sideways_only: bool = False
    apex_supertrend_period: int = 7
    apex_supertrend_multiplier: float = 3.0
    apex_max_trades_per_day: int = 3
    apex_use_hedged_spreads: bool = True
    require_ema_cross_for_credit: bool = True
    exit_credit_on_ema_cross_flip: bool = True
    credit_iron_condor_without_cross: bool = False
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    breakout_lookback: int = 20
    require_supertrend_align: bool = True
    require_breakout_tag: bool = False
    entry_confirmation_bars: int = 2
    min_directional_ema_spread_pct: float = 0.015
    max_cpr_entry_extension_pct: float = 0.0
    max_sideways_ema_spread_pct: float = 0.08
    breakout_confidence_boost: float = 0.06
    exit_on_supertrend_flip: bool = True
    exit_credit_on_signal_flip: bool = True
    reentry_cooldown_bars: int = 0
    credit_spot_stop_pct: float = 0.012
    enforce_cost_economics: bool = True
    min_edge_to_cost_multiple: float = 1.5
    exit_buy_on_cloud_reentry: bool = False
    ichimoku_conversion_period: int = 9
    ichimoku_base_period: int = 26
    ichimoku_span_b_period: int = 52
    cpr_narrow_width_pct: float = 0.35
    cpr_wide_width_pct: float = 0.75
    enable_credit_strategies: bool = True
    credit_wing_strikes: int = 2
    credit_short_strike_steps: int = 2
    credit_min_confidence: float = 0.58
    credit_min_volume_ratio: float = 0.85
    credit_volume_lookback_bars: int = 20
    credit_min_reward_to_risk: float = 0.12
    buy_min_volume_ratio: float = 0.85
    buy_volume_lookback_bars: int = 20
    auto_buy_trending_only: bool = True
    credit_profit_target_pct: float = 0.50
    credit_stop_loss_pct: float = 0.60
    enable_profit_trail: bool = True
    profit_trail_arm_rupees_per_lot: float = 500.0
    profit_trail_giveback_pct: float = 0.25


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
    if not os.getenv("PYTEST_CURRENT_TEST"):
        load_dotenv(ENV_PATH, override=False)
    return StrategyParams(
        ema_fast_period=_int("EMA_FAST_PERIOD", 8),
        ema_slow_period=_int("EMA_SLOW_PERIOD", 20),
        auto_intelligent_routing=_bool("AUTO_INTELLIGENT_ROUTING", True),
        auto_include_apex=_bool("AUTO_INCLUDE_APEX", False),
        auto_trend_buy_first=_bool("AUTO_TREND_BUY_FIRST", True),
        auto_credit_sideways_only=_bool("AUTO_CREDIT_SIDEWAYS_ONLY", False),
        apex_supertrend_period=_int("APEX_SUPERTREND_PERIOD", 7),
        apex_supertrend_multiplier=_float("APEX_SUPERTREND_MULTIPLIER", 3.0),
        apex_max_trades_per_day=_int("APEX_MAX_TRADES_PER_DAY", 3),
        apex_use_hedged_spreads=_bool("APEX_USE_HEDGED_SPREADS", True),
        require_ema_cross_for_credit=_bool("REQUIRE_EMA_CROSS_FOR_CREDIT", True),
        exit_credit_on_ema_cross_flip=_bool("EXIT_CREDIT_ON_EMA_CROSS_FLIP", True),
        credit_iron_condor_without_cross=_bool("CREDIT_IRON_CONDOR_WITHOUT_CROSS", False),
        supertrend_period=_int("SUPERTREND_PERIOD", 10),
        supertrend_multiplier=_float("SUPERTREND_MULTIPLIER", 3.0),
        breakout_lookback=_int("BREAKOUT_LOOKBACK", 20),
        require_supertrend_align=_bool("REQUIRE_SUPERTREND_ALIGN", True),
        require_breakout_tag=_bool("REQUIRE_BREAKOUT_TAG", False),
        entry_confirmation_bars=_int("ENTRY_CONFIRMATION_BARS", 2),
        min_directional_ema_spread_pct=_float("MIN_DIRECTIONAL_EMA_SPREAD_PCT", 0.015),
        max_cpr_entry_extension_pct=_float("MAX_CPR_ENTRY_EXTENSION_PCT", 0.0),
        max_sideways_ema_spread_pct=_float("MAX_SIDEWAYS_EMA_SPREAD_PCT", 0.08),
        breakout_confidence_boost=_float("BREAKOUT_CONFIDENCE_BOOST", 0.06),
        exit_on_supertrend_flip=_bool("EXIT_ON_SUPERTREND_FLIP", True),
        exit_credit_on_signal_flip=_bool("EXIT_CREDIT_ON_SIGNAL_FLIP", True),
        reentry_cooldown_bars=_int("REENTRY_COOLDOWN_BARS", 0),
        credit_spot_stop_pct=_float("CREDIT_SPOT_STOP_PCT", 0.012),
        enforce_cost_economics=_bool("ENFORCE_COST_ECONOMICS", True),
        min_edge_to_cost_multiple=_float("MIN_EDGE_TO_COST_MULTIPLE", 1.5),
        exit_buy_on_cloud_reentry=_bool("EXIT_BUY_ON_CLOUD_REENTRY", False),
        ichimoku_conversion_period=_int("ICHIMOKU_CONVERSION_PERIOD", 9),
        ichimoku_base_period=_int("ICHIMOKU_BASE_PERIOD", 26),
        ichimoku_span_b_period=_int("ICHIMOKU_SPAN_B_PERIOD", 52),
        cpr_narrow_width_pct=_float("CPR_NARROW_WIDTH_PCT", 0.35),
        cpr_wide_width_pct=_float("CPR_WIDE_WIDTH_PCT", 0.75),
        enable_credit_strategies=_bool("ENABLE_CREDIT_STRATEGIES", True),
        credit_wing_strikes=_int("CREDIT_WING_STRIKES", 2),
        credit_short_strike_steps=_int("CREDIT_SHORT_STRIKE_STEPS", 2),
        credit_min_confidence=_float("CPR_CREDIT_MIN_CONFIDENCE", 0.58),
        credit_min_volume_ratio=_float("CREDIT_MIN_VOLUME_RATIO", 0.85),
        credit_volume_lookback_bars=_int("CREDIT_VOLUME_LOOKBACK_BARS", 20),
        credit_min_reward_to_risk=_float("CREDIT_MIN_REWARD_TO_RISK", 0.12),
        buy_min_volume_ratio=_float("BUY_MIN_VOLUME_RATIO", 0.85),
        buy_volume_lookback_bars=_int("BUY_VOLUME_LOOKBACK_BARS", 20),
        auto_buy_trending_only=_bool("AUTO_BUY_TRENDING_ONLY", True),
        credit_profit_target_pct=_float("CREDIT_PROFIT_TARGET_PCT", 0.50),
        credit_stop_loss_pct=_float("CREDIT_STOP_LOSS_PCT", 0.60),
        enable_profit_trail=_bool("ENABLE_PROFIT_TRAIL", True),
        profit_trail_arm_rupees_per_lot=_float("PROFIT_TRAIL_ARM_RUPEES_PER_LOT", 500.0),
        profit_trail_giveback_pct=_float("PROFIT_TRAIL_GIVEBACK_PCT", 0.25),
    )


def strategy_tuning_summary() -> dict[str, object]:
    """Active tuning values for dashboard / API (edit .env, then restart)."""
    from index_ai.strategies.strategy_router import strategy_style

    from index_ai.config import candle_interval_int, candle_interval_minutes

    p = get_strategy_params()
    style = strategy_style()
    iv = candle_interval_minutes()
    iv_int = candle_interval_int()
    confirm_min = p.entry_confirmation_bars * iv_int
    breakout_min = p.breakout_lookback * iv_int
    return {
        "strategy_style": style,
        "candle_interval_minutes": iv,
        "candle_bar_note": (
            f"All EMA/CPR/Supertrend signals use {iv}m spot bars "
            f"({p.entry_confirmation_bars} bars ≈ {confirm_min} min confirm, "
            f"breakout lookback {p.breakout_lookback} bars ≈ {breakout_min} min)."
        ),
        "strategy_style_note": {
            "AUTO": (
                "Trending CPR → long premium (calls/puts); sideways → iron condor / CPR credit. "
                "1m EMA cross + volume gates on sell lane. No Apex in AUTO."
                if p.auto_intelligent_routing
                else (
                    "Trending CPR → long premium only; sideways → no buying. "
                    "Credit spreads via CPR + EMA + volume."
                    if p.auto_buy_trending_only and not p.auto_credit_sideways_only
                    else "Fixed rules from REQUIRE_EMA_CROSS_FOR_CREDIT / CPR credit."
                )
            ),
            "CREDIT": "Only hedged credit (EMA cross when REQUIRE_EMA_CROSS_FOR_CREDIT=true).",
            "BUY": "Only long premium (calls/puts).",
            "APEX": (
                "Pivot R1/S1 + Supertrend (7,3): sell ATM put above R1, ATM call below S1. "
                + ("Hedged spreads by default." if p.apex_use_hedged_spreads else "Naked ATM sell.")
            ),
        }.get(style, ""),
        "auto_intelligent_routing": p.auto_intelligent_routing,
        "auto_include_apex": p.auto_include_apex,
        "auto_trend_buy_first": p.auto_trend_buy_first,
        "auto_credit_sideways_only": p.auto_credit_sideways_only,
        "auto_buy_trending_only": p.auto_buy_trending_only,
        "apex_supertrend_period": p.apex_supertrend_period,
        "apex_supertrend_multiplier": p.apex_supertrend_multiplier,
        "apex_max_trades_per_day": p.apex_max_trades_per_day,
        "apex_use_hedged_spreads": p.apex_use_hedged_spreads,
        "apex_note": (
            f"R1/S1 pivots + ST {p.apex_supertrend_period}/{p.apex_supertrend_multiplier}, "
            f"max {p.apex_max_trades_per_day} trades/index/day, no entry after 15:00 IST."
        ),
        "ema_fast_period": p.ema_fast_period,
        "ema_slow_period": p.ema_slow_period,
        "require_ema_cross_for_credit": p.require_ema_cross_for_credit,
        "exit_credit_on_ema_cross_flip": p.exit_credit_on_ema_cross_flip,
        "credit_iron_condor_without_cross": p.credit_iron_condor_without_cross,
        "ema_cross_note": (
            f"Spot {iv}m chart — EMA {p.ema_fast_period}/{p.ema_slow_period}. "
            + (
                "AUTO intelligently picks cross / range / trend credit each scan."
                if p.auto_intelligent_routing
                else (
                    "Credit entries only on fresh cross; exits when alignment flips."
                    if p.require_ema_cross_for_credit
                    else "CPR regime drives credit (legacy)."
                )
            )
        ),
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
        "credit_min_volume_ratio": p.credit_min_volume_ratio,
        "credit_volume_lookback_bars": p.credit_volume_lookback_bars,
        "credit_min_reward_to_risk": p.credit_min_reward_to_risk,
        "buy_min_volume_ratio": p.buy_min_volume_ratio,
        "buy_volume_lookback_bars": p.buy_volume_lookback_bars,
        "credit_profit_target_pct": p.credit_profit_target_pct,
        "credit_stop_loss_pct": p.credit_stop_loss_pct,
        "enable_profit_trail": p.enable_profit_trail,
        "profit_trail_arm_rupees_per_lot": p.profit_trail_arm_rupees_per_lot,
        "profit_trail_giveback_pct": p.profit_trail_giveback_pct,
        "profit_trail_note": (
            "No fixed profit cap when enabled — MTM trails peak profit; "
            f"arms after ₹{p.profit_trail_arm_rupees_per_lot:,.0f}×lots, "
            f"exits on {p.profit_trail_giveback_pct:.0%} giveback from peak."
        ),
        "enforce_cost_economics": p.enforce_cost_economics,
        "min_edge_to_cost_multiple": p.min_edge_to_cost_multiple,
        "cost_economics_note": (
            "Entry is blocked unless the trade's expected edge (credit collected, "
            "or the index move to trail-arm × delta × qty) is at least "
            f"{p.min_edge_to_cost_multiple:g}× the estimated round-trip cost "
            "(brokerage + STT + exchange + GST + stamp + half-spread slippage)."
        ),
        "exit_buy_on_cloud_reentry": p.exit_buy_on_cloud_reentry,
        "ichimoku_conversion_period": p.ichimoku_conversion_period,
        "ichimoku_base_period": p.ichimoku_base_period,
        "ichimoku_span_b_period": p.ichimoku_span_b_period,
        "cloud_exit_note": (
            "Buy lane only: exit long-premium when spot closes back into the Kumo "
            f"(Ichimoku {p.ichimoku_conversion_period}/{p.ichimoku_base_period}/"
            f"{p.ichimoku_span_b_period}). Longs trail the cloud top, shorts the Kijun."
        ),
        "require_supertrend_align": p.require_supertrend_align,
        "require_breakout_tag": p.require_breakout_tag,
        "breakout_lookback": p.breakout_lookback,
        "entry_confirmation_bars": p.entry_confirmation_bars,
        "min_directional_ema_spread_pct": p.min_directional_ema_spread_pct,
        "max_cpr_entry_extension_pct": p.max_cpr_entry_extension_pct,
        "max_sideways_ema_spread_pct": p.max_sideways_ema_spread_pct,
        "supertrend_period": p.supertrend_period,
        "supertrend_multiplier": p.supertrend_multiplier,
        "env_keys": [
            "CANDLE_INTERVAL_MINUTES",
            "STRATEGY_STYLE",
            "APEX_SUPERTREND_PERIOD",
            "APEX_SUPERTREND_MULTIPLIER",
            "APEX_MAX_TRADES_PER_DAY",
            "APEX_USE_HEDGED_SPREADS",
            "APEX_ENTRIES_START",
            "APEX_NO_ENTRY_AFTER",
            "AUTO_INTELLIGENT_ROUTING",
            "AUTO_INCLUDE_APEX",
            "AUTO_TREND_BUY_FIRST",
            "AUTO_CREDIT_SIDEWAYS_ONLY",
            "EMA_FAST_PERIOD",
            "EMA_SLOW_PERIOD",
            "REQUIRE_EMA_CROSS_FOR_CREDIT",
            "EXIT_CREDIT_ON_EMA_CROSS_FLIP",
            "CREDIT_IRON_CONDOR_WITHOUT_CROSS",
            "ENABLE_CREDIT_STRATEGIES",
            "CPR_NARROW_WIDTH_PCT",
            "CPR_WIDE_WIDTH_PCT",
            "CPR_CREDIT_MIN_CONFIDENCE",
            "CREDIT_MIN_VOLUME_RATIO",
            "CREDIT_VOLUME_LOOKBACK_BARS",
            "CREDIT_MIN_REWARD_TO_RISK",
            "BUY_MIN_VOLUME_RATIO",
            "BUY_VOLUME_LOOKBACK_BARS",
            "CREDIT_WING_STRIKES",
            "CREDIT_SHORT_STRIKE_STEPS",
            "CREDIT_PROFIT_TARGET_PCT",
            "CREDIT_STOP_LOSS_PCT",
            "ENABLE_PROFIT_TRAIL",
            "PROFIT_TRAIL_ARM_RUPEES_PER_LOT",
            "PROFIT_TRAIL_GIVEBACK_PCT",
            "REQUIRE_SUPERTREND_ALIGN",
            "REQUIRE_BREAKOUT_TAG",
            "BREAKOUT_LOOKBACK",
            "ENTRY_CONFIRMATION_BARS",
            "MIN_DIRECTIONAL_EMA_SPREAD_PCT",
            "MAX_CPR_ENTRY_EXTENSION_PCT",
            "MAX_SIDEWAYS_EMA_SPREAD_PCT",
            "SUPERTREND_PERIOD",
            "SUPERTREND_MULTIPLIER",
            "EXIT_BUY_ON_CLOUD_REENTRY",
            "ICHIMOKU_CONVERSION_PERIOD",
            "ICHIMOKU_BASE_PERIOD",
            "ICHIMOKU_SPAN_B_PERIOD",
            "ENFORCE_COST_ECONOMICS",
            "MIN_EDGE_TO_COST_MULTIPLE",
            "CHARGE_BROKERAGE_PER_ORDER",
            "CHARGE_STT_SELL_PCT",
            "CHARGE_EXCH_TXN_PCT_NSE",
            "CHARGE_EXCH_TXN_PCT_BSE",
            "CHARGE_GST_PCT",
            "CHARGE_STAMP_BUY_PCT",
            "SLIPPAGE_HALF_SPREAD_POINTS_NIFTY",
            "SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY",
            "SLIPPAGE_HALF_SPREAD_POINTS_SENSEX",
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
