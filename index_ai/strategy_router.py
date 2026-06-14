"""Route CPR regime → buy premium vs hedged credit selling."""

from __future__ import annotations

import os

from index_ai.cpr_regime import CprRegime, analyze_cpr_regime
from index_ai.ema_cross import analyze_ema_cross, credit_action_for_cross
from index_ai.strategy import StrategySignal, add_indicators, copy_signal, intraday_strategy_signal
from index_ai.intelligent_router import choose_auto_engine
from index_ai.strategy_mode import pick_auto_credit
from index_ai.strategy_params import get_strategy_params
import pandas as pd


def strategy_style() -> str:
    raw = os.getenv("STRATEGY_STYLE", "AUTO").strip().upper()
    if raw not in {"AUTO", "BUY", "CREDIT", "APEX"}:
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


def _credit_confidence(regime: CprRegime, *, ema_cross: bool, strategy_mode: str) -> float:
    base = get_strategy_params().credit_min_confidence
    if ema_cross or strategy_mode == "ema_cross":
        base = max(base, 0.60)
    if strategy_mode == "cpr_trend":
        base = max(base, 0.59)
    if regime.width_class == "WIDE" and regime.day_bias == "SIDEWAYS":
        base = 0.64
    if regime.width_class == "NARROW" and regime.day_bias.startswith("TRENDING"):
        base = 0.62
    if regime.virgin_cpr:
        base = min(0.72, base + 0.04)
    return round(base, 3)


def _attach_ema_fields(signal: StrategySignal, cross: dict) -> StrategySignal:
    if not cross.get("ready"):
        return signal
    return copy_signal(
        signal,
        ema_fast=float(cross["ema_fast"]),
        ema_slow=float(cross["ema_slow"]),
        ema_cross=str(cross.get("cross") or ""),
        ema_aligned=str(cross.get("aligned") or ""),
    )


def _with_range_quality(
    signal: StrategySignal,
    frame: pd.DataFrame,
    params,
) -> StrategySignal:
    if signal.action != "SELL_IRON_CONDOR":
        return signal
    row = frame.iloc[-1]
    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    spread_pct = abs(ema_fast - ema_slow) / max(abs(price), 1.0) * 100.0
    max_spread = max(0.0, float(params.max_sideways_ema_spread_pct))
    if max_spread and spread_pct > max_spread:
        return copy_signal(
            signal,
            action="NO_TRADE",
            reason=(
                f"Range setup skipped: EMA spread {spread_pct:.3f}% is above sideways gate "
                f"{max_spread:.3f}%."
            ),
            confidence=0.0,
            ema_spread_pct=round(spread_pct, 4),
            entry_quality="range_chop_guard",
        )
    return copy_signal(
        signal,
        ema_spread_pct=round(spread_pct, 4),
        entry_quality="sideways_range_confirmed",
    )


def _resolve_credit_action(
    regime: CprRegime,
    cross: dict,
    *,
    params,
    style: str,
) -> tuple[str | None, str, str]:
    """Return (action, reason, strategy_mode)."""
    if style == "AUTO" and params.auto_intelligent_routing:
        return pick_auto_credit(
            regime,
            cross,
            ema_fast=params.ema_fast_period,
            ema_slow=params.ema_slow_period,
        )

    if params.require_ema_cross_for_credit:
        action = credit_action_for_cross(cross)
        if action:
            label = "bullish" if action == "SELL_BULL_PUT_SPREAD" else "bearish"
            return (
                action,
                (
                    f"EMA {params.ema_fast_period}/{params.ema_slow_period} {label} cross on spot. "
                    f"{regime.note}"
                ),
                "ema_cross",
            )
        if params.credit_iron_condor_without_cross and regime.day_bias == "SIDEWAYS":
            return (
                "SELL_IRON_CONDOR",
                f"{regime.note} Sideways CPR — iron condor (no EMA cross this bar).",
                "cpr_sideways",
            )
        return (
            None,
            (
                f"No fresh EMA {params.ema_fast_period}/{params.ema_slow_period} cross "
                f"(aligned {cross.get('aligned') or 'n/a'})."
            ),
            "wait",
        )

    action = _credit_action_for_regime(regime)
    if not action:
        return None, regime.note, "wait"
    return action, f"{regime.note} → {action.replace('_', ' ').title()}.", "cpr_trend"


def route_intraday_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    allow_option_selling: bool = True,
    allow_option_buying: bool = True,
) -> tuple[StrategySignal, CprRegime]:
    params = get_strategy_params()
    style = strategy_style()
    frame = (
        add_indicators(today, fast=params.ema_fast_period, slow=params.ema_slow_period)
        if "ema_fast" not in today.columns
        else today
    )
    cross = analyze_ema_cross(
        frame, fast=params.ema_fast_period, slow=params.ema_slow_period
    )
    row = frame.iloc[-1]
    regime = analyze_cpr_regime(
        frame,
        previous_day,
        price=float(row["close"]),
        ema_fast=float(row["ema_fast"]),
        ema_slow=float(row["ema_slow"]),
    )

    if style == "APEX":
        from index_ai.apex_pivot_trend import apex_pivot_trend_signal

        signal = apex_pivot_trend_signal(frame, previous_day)
        if signal.action != "NO_TRADE":
            signal = enrich_signal_with_regime(signal, regime)
        return _attach_ema_fields(signal, cross), regime

    buy_signal = intraday_strategy_signal(frame, previous_day, params=params)
    buy = enrich_signal_with_regime(buy_signal, regime)
    if buy.action in {"BUY_CALL", "BUY_PUT"}:
        buy = copy_signal(buy, strategy_mode="buy")

    if style == "AUTO" and allow_option_buying and params.auto_trend_buy_first:
        if regime.day_bias == "TRENDING_BULL" and buy.action == "BUY_CALL":
            return _attach_ema_fields(buy, cross), regime
        if regime.day_bias == "TRENDING_BEAR" and buy.action == "BUY_PUT":
            return _attach_ema_fields(buy, cross), regime

    credit_blocked_on_trend = (
        style == "AUTO"
        and params.auto_credit_sideways_only
        and regime.day_bias in {"TRENDING_BULL", "TRENDING_BEAR"}
    )
    try_credit = not credit_blocked_on_trend and (
        style == "CREDIT"
        or (style == "AUTO" and allow_option_selling and params.enable_credit_strategies)
    )

    if try_credit:
        credit_action: str | None
        credit_reason: str
        mode: str
        apex_conf = 0.0

        if (
            style == "AUTO"
            and params.auto_intelligent_routing
            and params.auto_include_apex
        ):
            choice = choose_auto_engine(
                frame,
                previous_day,
                regime,
                cross,
                params=params,
                close=float(row["close"]),
            )
            credit_action = choice.action
            credit_reason = choice.reason
            mode = choice.strategy_mode
            apex_conf = choice.apex_confidence
        else:
            credit_action, credit_reason, mode = _resolve_credit_action(
                regime, cross, params=params, style=style
            )

        use_regime_gate = (
            not params.auto_intelligent_routing and not params.require_ema_cross_for_credit
        )

        if credit_action and use_regime_gate and regime.day_bias == "MIXED":
            credit_action = None

        if credit_action:
            conf = (
                apex_conf
                if mode.startswith("apex")
                else _credit_confidence(
                    regime,
                    ema_cross=bool(cross.get("cross")),
                    strategy_mode=mode,
                )
            )
            signal = StrategySignal(
                action=credit_action,
                reason=credit_reason,
                confidence=conf,
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
                strategy_mode=mode,
            )
            signal = _with_range_quality(signal, frame, params)
            return _attach_ema_fields(signal, cross), regime

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
            strategy_mode="wait",
        )
        return _attach_ema_fields(signal, cross), regime

    if not allow_option_buying:
        signal = StrategySignal(
            action="NO_TRADE",
            reason="Option buying disabled (ALLOW_OPTION_BUYING=false).",
            confidence=0.0,
            price=float(row["close"]),
            pivot=regime.pivot,
            bc=regime.bc,
            tc=regime.tc,
            ema_fast=float(row["ema_fast"]),
            ema_slow=float(row["ema_slow"]),
            cpr_regime=regime.day_bias,
            strategy_mode="wait",
        )
        return _attach_ema_fields(signal, cross), regime

    if buy.action == "NO_TRADE" and style == "AUTO" and params.auto_intelligent_routing:
        suffix = (
            " AUTO: trending day — buy-only (credit reserved for sideways CPR)."
            if credit_blocked_on_trend
            else " AUTO: no credit or buy setup this scan."
        )
        buy = copy_signal(
            buy,
            reason=f"{buy.reason}{suffix}",
            strategy_mode="wait",
        )
    return _attach_ema_fields(buy, cross), regime
