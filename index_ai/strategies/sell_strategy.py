"""Directional credit selling — CPR support/resistance + 5m EMA + volume + 15m trend."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.credit_spread import map_premium_sell_to_hedged_credit
from index_ai.strategies.ema_cross import analyze_ema_cross
from index_ai.strategies.strategy import StrategySignal, add_indicators
from index_ai.strategies.strategy_mode import pick_auto_credit
from index_ai.strategies.strategy_params import StrategyParams, get_strategy_params
from index_ai.strategies.premium_sell import PREMIUM_SELL_ACTIONS


def _structure_for_bias(day_bias: str) -> str:
    return {
        "SIDEWAYS": "IRON_CONDOR",
        "TRENDING_BULL": "BULL_PUT_SPREAD",
        "TRENDING_BEAR": "BEAR_CALL_SPREAD",
    }.get(day_bias, "")


def _credit_confidence(
    regime: CprRegime,
    *,
    ema_cross: bool,
    strategy_mode: str,
    volume_ratio: float = 1.0,
) -> float:
    params = get_strategy_params()
    # Floor for a plain credit setup — the sell lane's take-the-trade bar. Mode-
    # specific overrides below raise it where more confirmation is wanted.
    base = params.credit_confidence_gate
    if ema_cross or strategy_mode == "ema_cross":
        base = max(base, 0.60)
    if strategy_mode == "cpr_sideways":
        base = max(base, 0.62)
    if strategy_mode == "cpr_trend":
        base = max(base, 0.59)
    if regime.width_class == "WIDE" and regime.day_bias == "SIDEWAYS":
        base = 0.64
    if regime.width_class == "NARROW" and regime.day_bias.startswith("TRENDING"):
        base = 0.62
    if regime.virgin_cpr:
        base = min(0.72, base + 0.04)
    if volume_ratio >= 1.2:
        base = min(0.75, base + 0.03)
    return round(base, 3)


def _naked_at_cpr_boundary(
    regime: CprRegime,
    price: float,
    *,
    allow_naked: bool,
) -> tuple[str | None, str]:
    if not allow_naked:
        return None, ""
    tol = 0.0012
    if regime.day_bias == "TRENDING_BULL" and price >= regime.tc * (1 - tol):
        return (
            "SELL_ATM_PUT",
            f"CPR sell: price at/above TC {regime.tc:.0f} — bullish bias, naked put. {regime.note}",
        )
    if regime.day_bias == "TRENDING_BEAR" and price <= regime.bc * (1 + tol):
        return (
            "SELL_ATM_CALL",
            f"CPR sell: price at/below BC {regime.bc:.0f} — bearish bias, naked call. {regime.note}",
        )
    return None, ""


_BULL_SELL = {"SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"}


def _trend15_block(
    action: str, price: float, trend15: dict[str, Any] | None, cfg: StrategyParams
) -> str | None:
    """Reason the 15m trend / swing S&R vetoes this credit, or None.

    A bullish credit (short puts) needs the 15m trend not to be down and price
    not to have broken the 15m swing low; mirror for a bearish credit.
    """
    if not trend15 or not trend15.get("ready") or not cfg.sell_require_trend15:
        return None
    bull = action in _BULL_SELL
    want = 1 if bull else -1
    d = int(trend15.get("direction") or 0)
    struct = str(trend15.get("structure") or "RANGE")
    if (d != 0 and d != want) or struct == ("DOWN" if bull else "UP"):
        return (
            f"15m trend opposes the {'bullish' if bull else 'bearish'} credit "
            f"(15m dir {d:+d}, structure {struct})."
        )
    lo, hi = float(trend15.get("swing_low") or 0.0), float(trend15.get("swing_high") or 0.0)
    if bull and lo > 0 and price < lo:
        return f"price {price:.0f} broke the 15m swing low {lo:.0f} — support gone."
    if not bull and hi > 0 and price > hi:
        return f"price {price:.0f} broke the 15m swing high {hi:.0f} — resistance gone."
    return None


def evaluate_sell_signal(
    frame: pd.DataFrame,
    previous_day: pd.DataFrame,
    regime: CprRegime,
    cross: dict[str, Any] | None = None,
    *,
    params: StrategyParams | None = None,
    trend15: dict[str, Any] | None = None,
) -> StrategySignal:
    """Option selling from CPR levels + 5m EMA + volume, gated by the 15m trend.

    ``trend15`` is ``strategy_mode.summarize_trend15`` output (direction + swing
    S/R from the 15-minute frame). When present and ``sell_require_trend15`` is
    set, a credit whose direction the 15m trend opposes — or one being sold
    straight through the 15m swing level — is dropped.
    """
    _ = previous_day
    cfg = params or get_strategy_params()
    df = (
        add_indicators(frame, fast=cfg.ema_fast_period, slow=cfg.ema_slow_period)
        if "ema_fast" not in frame.columns
        else frame
    )
    row = df.iloc[-1]
    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    cross = cross or analyze_ema_cross(df, fast=cfg.ema_fast_period, slow=cfg.ema_slow_period)

    base = dict(
        price=price,
        pivot=regime.pivot,
        bc=regime.bc,
        tc=regime.tc,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        cpr_width_pct=regime.width_pct,
        cpr_width_class=regime.width_class,
        cpr_regime=regime.day_bias,
        cpr_virgin=regime.virgin_cpr,
        recommended_structure=_structure_for_bias(regime.day_bias),
        ema_cross=str(cross.get("cross") or ""),
        ema_aligned=str(cross.get("aligned") or ""),
    )

    allow_naked = not cfg.apex_use_hedged_spreads

    action, reason, mode = pick_auto_credit(
        regime,
        cross,
        ema_fast=cfg.ema_fast_period,
        ema_slow=cfg.ema_slow_period,
        frame=df,
        trend15=trend15,
    )

    if not action:
        naked, naked_reason = _naked_at_cpr_boundary(regime, price, allow_naked=allow_naked)
        if naked and cross.get("cross"):
            # same tape veto pick_auto_credit applies — never let a vetoed hedged
            # spread fall through to an *unhedged* short in the same direction
            from index_ai.strategies.candlestick_sr import intraday_candle_trend

            tape = intraday_candle_trend(df, lookback=15)
            opposed = (naked == "SELL_ATM_PUT" and tape == "DOWN") or (
                naked == "SELL_ATM_CALL" and tape == "UP"
            )
            if not opposed:
                action = naked
                reason = naked_reason
                mode = "cpr_naked"

    if not action:
        return StrategySignal(
            action="NO_TRADE",
            reason=reason or f"No CPR sell setup ({regime.day_bias}).",
            confidence=0.0,
            strategy_mode=mode or "wait",
            **base,
        )

    blocked_15m = _trend15_block(action, price, trend15, cfg)
    if blocked_15m:
        return StrategySignal(
            action="NO_TRADE",
            reason=f"CPR sell skipped: {blocked_15m}",
            confidence=0.0,
            strategy_mode="wait",
            **base,
        )

    vol_ok, vol_stats = volume_confirms(
        df,
        min_ratio=cfg.credit_min_volume_ratio,
        lookback=cfg.credit_volume_lookback_bars,
    )
    if not vol_ok:
        return StrategySignal(
            action="NO_TRADE",
            reason=(
                f"CPR sell skipped: 5m volume {vol_stats.get('last_bar_volume', 0):,} "
                f"({float(vol_stats.get('ratio') or 0):.2f}x avg) below gate."
            ),
            confidence=0.0,
            strategy_mode="wait",
            **base,
        )

    conf = _credit_confidence(
        regime,
        ema_cross=bool(cross.get("cross")),
        strategy_mode=mode,
        volume_ratio=float(vol_stats.get("ratio") or 1.0),
    )

    if action in PREMIUM_SELL_ACTIONS and cfg.apex_use_hedged_spreads:
        hedged = map_premium_sell_to_hedged_credit(action)
        if hedged:
            action = hedged
            reason = f"{reason} (hedged spread, wings {cfg.credit_wing_strikes} steps)."
            mode = "cpr_hedged"

    if action == "SELL_IRON_CONDOR":
        spread_pct = abs(ema_fast - ema_slow) / max(abs(price), 1.0) * 100.0
        max_spread = max(0.0, float(cfg.max_sideways_ema_spread_pct))
        if max_spread and spread_pct > max_spread:
            return StrategySignal(
                action="NO_TRADE",
                reason=(
                    f"Sideways CPR iron condor skipped: EMA spread {spread_pct:.3f}% "
                    f"> gate {max_spread:.3f}%."
                ),
                confidence=0.0,
                strategy_mode="wait",
                ema_spread_pct=round(spread_pct, 4),
                **base,
            )

    vol_note = ""
    if vol_stats.get("ready"):
        vol_note = f" Vol {vol_stats['last_bar_volume']:,} ({vol_stats['ratio']:.2f}x avg)."

    return StrategySignal(
        action=action,
        reason=f"Sell: {reason}{vol_note}",
        confidence=conf,
        strategy_mode=mode,
        entry_quality="cpr_credit",
        volume_ratio=float(vol_stats.get("ratio") or 1.0),
        **base,
    )
