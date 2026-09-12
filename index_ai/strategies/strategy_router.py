"""Route buy (candlestick) and sell (CPR) strategies independently."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from index_ai.options_oi import OptionOiContext
from index_ai.strategies.buy_strategy import evaluate_buy_signal
from index_ai.strategies.cpr_regime import CprRegime, analyze_cpr_regime
from index_ai.strategies.credit_spread import CREDIT_ACTIONS
from index_ai.strategies.ema_cross import analyze_ema_cross, min_ema_bars
from index_ai.strategies.premium_sell import PREMIUM_SELL_ACTIONS, is_premium_sell_action
from index_ai.strategies.sell_strategy import evaluate_sell_signal
from index_ai.strategies.strategy import StrategySignal, add_indicators, copy_signal
from index_ai.strategies.strategy_mode import summarize_trend15
from index_ai.strategies.strategy_params import get_strategy_params


def strategy_style() -> str:
    raw = os.getenv("STRATEGY_STYLE", "AUTO").strip().upper()
    if raw not in {"AUTO", "BUY", "CREDIT"}:
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
    sell_regime: CprRegime  # 5m-frame CPR regime for the sell lane (== regime unless split)


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
        reason=(f"Buy: {buy.reason} | Sell: {sell.reason}" if sell.reason else buy.reason),
        strategy_mode="wait",
    )


def evaluate_dual_opportunities(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    allow_option_selling: bool = True,
    allow_option_buying: bool = True,
    sell_today: pd.DataFrame | None = None,
    sell_trend15: pd.DataFrame | None = None,
    oi: OptionOiContext | None = None,
) -> DualRouteResult:
    """``today`` drives the buy lane (fast interval). When ``sell_today`` (a 5m
    frame) and ``sell_trend15`` (a 15m frame) are supplied, the sell lane is
    evaluated on those instead — its own CPR regime, EMA cross and a 15m trend
    gate. Callers that pass neither keep the single-frame behaviour."""
    params = get_strategy_params()
    style = strategy_style()
    frame = (
        add_indicators(today, fast=params.ema_fast_period, slow=params.ema_slow_period)
        if "ema_fast" not in today.columns
        else today
    )
    cross = analyze_ema_cross(frame, fast=params.ema_fast_period, slow=params.ema_slow_period)
    row = frame.iloc[-1]
    regime = analyze_cpr_regime(
        frame,
        previous_day,
        price=float(row["close"]),
        ema_fast=float(row["ema_fast"]),
        ema_slow=float(row["ema_slow"]),
    )

    buy = _empty_buy(regime, row)
    sell = _empty_sell(regime, row)
    sell_regime = regime

    if style in {"AUTO", "BUY"} and allow_option_buying:
        buy = evaluate_buy_signal(frame, previous_day, regime, params=params, oi=oi)

    if style in {"AUTO", "CREDIT"} and allow_option_selling and params.enable_credit_strategies:
        if sell_today is not None and len(sell_today) >= min_ema_bars(params.ema_slow_period):
            s_frame = add_indicators(
                sell_today, fast=params.ema_fast_period, slow=params.ema_slow_period
            )
            s_row = s_frame.iloc[-1]
            sell_regime = analyze_cpr_regime(
                s_frame,
                previous_day,
                price=float(s_row["close"]),
                ema_fast=float(s_row["ema_fast"]),
                ema_slow=float(s_row["ema_slow"]),
            )
            s_cross = analyze_ema_cross(
                s_frame, fast=params.ema_fast_period, slow=params.ema_slow_period
            )
            t15 = summarize_trend15(sell_trend15, params) if sell_trend15 is not None else None
            sell = evaluate_sell_signal(
                s_frame, previous_day, sell_regime, s_cross, params=params, trend15=t15, oi=oi
            )
        else:
            sell = evaluate_sell_signal(frame, previous_day, regime, cross, params=params, oi=oi)

    primary = _pick_primary(buy, sell)
    return DualRouteResult(
        primary=primary, buy=buy, sell=sell, regime=regime, cross=cross, sell_regime=sell_regime
    )


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
