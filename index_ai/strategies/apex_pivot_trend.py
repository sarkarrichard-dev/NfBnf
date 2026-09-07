"""
Apex Pivot-Trend option selling (Theta Gainers / AlgoRoom style).

Spot chart (interval from CANDLE_INTERVAL_MINUTES): pivot R1/S1 + Supertrend (7, 3).
- Close > R1 and ST bullish → sell ATM put
- Close < S1 and ST bearish → sell ATM call
- Inside R1–S1 → no trade (sideways filter)
Exits: Supertrend flip or session square-off (15:15 IST).
"""

from __future__ import annotations


import pandas as pd

from index_ai.config import candle_interval_minutes
from index_ai.strategies.pivot_points import classic_pivot_levels
from index_ai.strategies.strategy import StrategySignal, copy_signal
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.strategies.supertrend import compute_supertrend


def apex_supertrend(frame: pd.DataFrame) -> pd.DataFrame:
    p = get_strategy_params()
    return compute_supertrend(
        frame,
        period=p.apex_supertrend_period,
        multiplier=p.apex_supertrend_multiplier,
    )


def apex_pivot_trend_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
) -> StrategySignal:
    """Generate SELL_ATM_PUT / SELL_ATM_CALL / NO_TRADE from pivot + supertrend."""
    if len(today) < 3:
        raise ValueError("Need at least 3 intraday candles for Apex Pivot-Trend.")
    pp, r1, s1, _ = classic_pivot_levels(previous_day)
    st_frame = apex_supertrend(today)
    row = st_frame.iloc[-1]
    close = float(row["close"])
    st_dir = int(row["supertrend_direction"])
    st_stop = float(row["supertrend"])
    in_range = s1 <= close <= r1

    base = StrategySignal(
        action="NO_TRADE",
        reason="Inside R1–S1 range — Apex waits for breakout (sideways filter).",
        confidence=0.0,
        price=close,
        pivot=pp,
        bc=s1,
        tc=r1,
        ema_fast=0.0,
        ema_slow=0.0,
        supertrend_direction=st_dir,
        supertrend_stop=st_stop,
        strategy_mode="apex",
        cpr_regime="SIDEWAYS" if in_range else "",
    )

    if in_range:
        return base

    p = get_strategy_params()
    iv = candle_interval_minutes()
    if close > r1 and st_dir == 1:
        gap = min(1.0, (close - r1) / max(close * 0.003, 1.0))
        conf = round(0.58 + gap * 0.12, 3)
        return copy_signal(
            base,
            action="SELL_ATM_PUT",
            reason=(
                f"Apex: {iv}m close {close:g} above R1 {r1:g} with Supertrend "
                f"({p.apex_supertrend_period},"
                f"{p.apex_supertrend_multiplier}) bullish — sell ATM put."
            ),
            confidence=conf,
        )

    if close < s1 and st_dir == -1:
        gap = min(1.0, (s1 - close) / max(close * 0.003, 1.0))
        conf = round(0.58 + gap * 0.12, 3)
        return copy_signal(
            base,
            action="SELL_ATM_CALL",
            reason=(
                f"Apex: {iv}m close {close:g} below S1 {s1:g} with Supertrend "
                f"({p.apex_supertrend_period},"
                f"{p.apex_supertrend_multiplier}) bearish — sell ATM call."
            ),
            confidence=conf,
        )

    if close > r1:
        return copy_signal(
            base,
            reason=f"Above R1 {r1:g} but Supertrend not bullish — no Apex entry.",
            strategy_mode="apex_wait",
        )
    if close < s1:
        return copy_signal(
            base,
            reason=f"Below S1 {s1:g} but Supertrend not bearish — no Apex entry.",
            strategy_mode="apex_wait",
        )
    return base


def map_apex_to_hedged_credit(action: str) -> str | None:
    """Optional safer structure: same direction as video but defined-risk spread."""
    act = str(action or "").upper()
    if act == "SELL_ATM_PUT":
        return "SELL_BULL_PUT_SPREAD"
    if act == "SELL_ATM_CALL":
        return "SELL_BEAR_CALL_SPREAD"
    return None
