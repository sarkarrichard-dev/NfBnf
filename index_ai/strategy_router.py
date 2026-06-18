"""Route buy (candlestick) and sell (CPR) strategies independently."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from index_ai.buy_strategy import evaluate_buy_signal
from index_ai.cpr_regime import CprRegime, analyze_cpr_regime
from index_ai.credit_spread import CREDIT_ACTIONS
from index_ai.ema_cross import analyze_ema_cross
from index_ai.premium_sell import PREMIUM_SELL_ACTIONS, is_premium_sell_action
from index_ai.sell_strategy import evaluate_sell_signal
from index_ai.strategy import StrategySignal, add_indicators, copy_signal
from index_ai.strategy_params import get_strategy_params


def strategy_style() -> str:
    raw = os.getenv("STRATEGY_STYLE", "AUTO").strip().upper()
    if raw not in {"AUTO", "BUY", "CREDIT", "APEX"}:
        return "AUTO"
    return raw


BUY_ACTIONS = frozenset({"BUY_CALL", "BUY_PUT"})
SELL_ACTIONS = frozenset(CREDIT_ACTIONS) | frozenset(PREMIUM_SELL_ACTIONS)


def trade_lane(action: str) -> str:
    act = str(action or "").upper()
    if act in BUY_ACTIONS:
        return "buy"
    if act in SELL_ACTIONS or is_premium_sell_action(act):
        return "sell"
    return "none"


@dataclass(frozen=True)
class DualRouteResult:
    """Independent buy + sell evaluation; primary is highest-confidence opportunity."""

    primary: StrategySignal
    buy: StrategySignal
    sell: StrategySignal
    regime: CprRegime
    cross: dict


def _empty_buy(regime: CprRegime, row, *, reason: str = "Buy lane disabled.") -> StrategySignal:
    return StrategySignal(
        action="NO_TRADE",
        reason=reason,
        confidence=0.0,
        price=float(row["close"]),
        pivot=regime.pivot,
        bc=regime.bc,
        tc=regime.tc,
        ema_fast=float(row.get("ema_fast", row["close"])),
        ema_slow=float(row.get("ema_slow", row["close"])),
        cpr_regime=regime.day_bias,
        strategy_mode="buy_off",
    )


def _empty_sell(regime: CprRegime, row, *, reason: str = "Sell lane disabled.") -> StrategySignal:
    return StrategySignal(
        action="NO_TRADE",
        reason=reason,
        confidence=0.0,
        price=float(row["close"]),
        pivot=regime.pivot,
        bc=regime.bc,
        tc=regime.tc,
        ema_fast=float(row.get("ema_fast", row["close"])),
        ema_slow=float(row.get("ema_slow", row["close"])),
        cpr_regime=regime.day_bias,
        strategy_mode="sell_off",
    )


def _pick_primary(buy: StrategySignal, sell: StrategySignal) -> StrategySignal:
    buy_ok = buy.action != "NO_TRADE"
    sell_ok = sell.action != "NO_TRADE"
    if buy_ok and sell_ok:
        return sell if sell.confidence >= buy.confidence else buy
    if sell_ok:
        return sell
    if buy_ok:
        return buy
    return copy_signal(
        buy,
        reason=(
            f"Buy: {buy.reason} | Sell: {sell.reason}"
            if sell.reason
            else buy.reason
        ),
        strategy_mode="wait",
    )


def evaluate_dual_opportunities(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    allow_option_selling: bool = True,
    allow_option_buying: bool = True,
) -> DualRouteResult:
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

        apex = apex_pivot_trend_signal(frame, previous_day)
        sell = copy_signal(
            apex,
            cpr_regime=regime.day_bias,
            pivot=regime.pivot,
            bc=regime.bc,
            tc=regime.tc,
            strategy_mode=apex.strategy_mode or "apex",
        )
        buy = _empty_buy(regime, row, reason="APEX mode — sell only.")
        primary = sell if sell.action != "NO_TRADE" else buy
        return DualRouteResult(primary=primary, buy=buy, sell=sell, regime=regime, cross=cross)

    buy = _empty_buy(regime, row)
    sell = _empty_sell(regime, row)

    if style in {"AUTO", "BUY"} and allow_option_buying:
        buy = evaluate_buy_signal(frame, previous_day, regime, params=params)

    if style in {"AUTO", "CREDIT"} and allow_option_selling and params.enable_credit_strategies:
        sell = evaluate_sell_signal(
            frame, previous_day, regime, cross, params=params
        )

    primary = _pick_primary(buy, sell)
    return DualRouteResult(primary=primary, buy=buy, sell=sell, regime=regime, cross=cross)


def route_intraday_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    allow_option_selling: bool = True,
    allow_option_buying: bool = True,
) -> tuple[StrategySignal, CprRegime]:
    """Backward-compatible entry: returns primary signal + CPR regime."""
    dual = evaluate_dual_opportunities(
        today,
        previous_day,
        allow_option_selling=allow_option_selling,
        allow_option_buying=allow_option_buying,
    )
    return dual.primary, dual.regime


def enrich_signal_with_regime(signal: StrategySignal, regime: CprRegime) -> StrategySignal:
    """Legacy helper — attach CPR fields if missing."""
    return copy_signal(
        signal,
        pivot=regime.pivot,
        bc=regime.bc,
        tc=regime.tc,
        cpr_width_pct=regime.width_pct,
        cpr_width_class=regime.width_class,
        cpr_regime=regime.day_bias,
        cpr_virgin=regime.virgin_cpr,
    )
