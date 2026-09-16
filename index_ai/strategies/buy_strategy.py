"""Long premium — candlestick patterns at candle S/R (CPR is context only)."""

from __future__ import annotations

import pandas as pd

from index_ai.options_oi import OptionOiContext, oi_walls
from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.candlestick_patterns import detect_candlestick_setup
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.strategy import (
    StrategySignal,
    add_indicators,
    previous_day_cpr,
)
from index_ai.strategies.strategy_params import StrategyParams, get_strategy_params
from index_ai.strategies.supertrend import supertrend_snapshot


def evaluate_buy_signal(
    frame: pd.DataFrame,
    previous_day: pd.DataFrame,
    regime: CprRegime,
    *,
    params: StrategyParams | None = None,
    oi: OptionOiContext | None = None,
) -> StrategySignal:
    """
    Option buying from OHLC patterns + support/resistance.

    S/R is the real option-chain OI walls (max-put/max-call OI strikes) when
    ``oi`` gives a clean read — the same walls the sell lane already uses —
    else the candle-range guess, same as before. CPR regime is attached for
    dashboard context but does NOT block mid-day trending patterns that
    develop from candle structure.
    """
    cfg = params or get_strategy_params()
    pattern_lb = min(int(cfg.breakout_lookback), 30)
    min_bars = max(5, pattern_lb + 3, 15)
    if len(frame) < min_bars:
        raise ValueError(f"Need at least {min_bars} intraday candles for buy signal.")

    df = add_indicators(frame, fast=cfg.ema_fast_period, slow=cfg.ema_slow_period)
    row = df.iloc[-1]
    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    pivot, bc, tc = previous_day_cpr(previous_day)

    walls = oi_walls(oi)
    setup = detect_candlestick_setup(
        df,
        sr_lookback=max(20, cfg.breakout_lookback),
        trend_lookback=15,
        breakout_lookback=cfg.breakout_lookback,
        breakout_confirm_bars=cfg.entry_confirmation_bars,
        oi_support=walls[0] if walls else None,
        oi_resistance=walls[1] if walls else None,
    )
    st = supertrend_snapshot(
        df,
        period=cfg.supertrend_period,
        multiplier=cfg.supertrend_multiplier,
    )
    volume_ok, volume_stats = volume_confirms(
        df,
        min_ratio=cfg.buy_min_volume_ratio,
        lookback=cfg.buy_volume_lookback_bars,
    )

    base_fields = dict(
        price=price,
        pivot=pivot,
        bc=bc,
        tc=tc,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        cpr_width_pct=regime.width_pct,
        cpr_width_class=regime.width_class,
        cpr_regime=regime.day_bias,
        cpr_virgin=regime.virgin_cpr,
        strategy_mode="candlestick_buy",
        supertrend_direction=int(st["direction"]) if st.get("ready") else 0,
        supertrend_stop=float(st["stop"]) if st.get("ready") else 0.0,
        breakout_tag=str((setup.get("breakout") or {}).get("breakout_tag") or ""),
        volume_ratio=float(volume_stats.get("ratio") or 1.0),
        sr_source=str(setup.get("sr_source") or "candle"),
    )

    if not setup.get("ready"):
        return StrategySignal(
            action="NO_TRADE",
            reason="No candlestick pattern at support/resistance this bar.",
            confidence=0.0,
            entry_quality="no_pattern",
            **base_fields,
        )

    if not volume_ok:
        return StrategySignal(
            action="NO_TRADE",
            reason=(
                f"Buy setup skipped: bar volume {volume_stats.get('last_bar_volume', 0):,} "
                f"({float(volume_stats.get('ratio') or 0):.2f}x avg) below confirmation gate."
            ),
            confidence=0.0,
            entry_quality="weak_volume",
            **base_fields,
        )

    direction = str(setup.get("direction") or "none")
    pattern = str(setup.get("pattern") or "")

    # 2026-09-16: a breakout is the one pattern here that's *betting the range
    # is over* — on a SIDEWAYS CPR day (the market itself reading as
    # directionless) that bet has the least going for it, and today's worst
    # loss was exactly this: a breakout_resistance buy while CPR read
    # SIDEWAYS. The reversal patterns (engulfing/hammer/shooting star) and
    # trend-pullback don't make this same bet, so they're not gated here.
    if pattern in {"breakout_resistance", "breakdown_support"} and regime.day_bias == "SIDEWAYS":
        return StrategySignal(
            action="NO_TRADE",
            reason=f"{setup['reason']} — CPR reads SIDEWAYS, breakout skipped.",
            confidence=0.0,
            entry_quality="cpr_sideways_veto",
            **base_fields,
        )

    conf = 0.58
    if pattern in {"bullish_engulfing", "bearish_engulfing"}:
        conf = 0.68
    if pattern in {"breakout_resistance", "breakdown_support"}:
        conf = 0.72
    if setup.get("intraday_trend") in {"UP", "DOWN"}:
        conf = min(0.78, conf + 0.04)

    if direction == "bull":
        if cfg.require_supertrend_align and st.get("ready") and st["direction"] != 1:
            return StrategySignal(
                action="NO_TRADE",
                reason=f"{setup['reason']} — Supertrend bearish, long skipped.",
                confidence=0.0,
                entry_quality="st_filter",
                ema_spread_pct=0.0,
                **base_fields,
            )
        if ema_fast < ema_slow:
            conf = max(0.55, conf - 0.05)
        return StrategySignal(
            action="BUY_CALL",
            reason=f"Buy: {setup['reason']}. CPR context: {regime.day_bias}.",
            confidence=round(conf, 3),
            entry_quality=str(setup.get("pattern") or "candlestick"),
            **base_fields,
        )

    if direction == "bear":
        if cfg.require_supertrend_align and st.get("ready") and st["direction"] != -1:
            return StrategySignal(
                action="NO_TRADE",
                reason=f"{setup['reason']} — Supertrend bullish, short skipped.",
                confidence=0.0,
                entry_quality="st_filter",
                ema_spread_pct=0.0,
                **base_fields,
            )
        if ema_fast > ema_slow:
            conf = max(0.55, conf - 0.05)
        return StrategySignal(
            action="BUY_PUT",
            reason=f"Buy: {setup['reason']}. CPR context: {regime.day_bias}.",
            confidence=round(conf, 3),
            entry_quality=str(setup.get("pattern") or "candlestick"),
            **base_fields,
        )

    return StrategySignal(
        action="NO_TRADE",
        reason="Candlestick scan inconclusive.",
        confidence=0.0,
        entry_quality="no_direction",
        **base_fields,
    )
