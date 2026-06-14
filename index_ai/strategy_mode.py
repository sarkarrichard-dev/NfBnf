"""Per-scan strategy mode selection for STRATEGY_STYLE=AUTO."""

from __future__ import annotations

from typing import Any

from index_ai.cpr_regime import CprRegime
from index_ai.ema_cross import credit_action_for_cross


def pick_auto_credit(
    regime: CprRegime,
    cross: dict[str, Any],
    *,
    ema_fast: int,
    ema_slow: int,
) -> tuple[str | None, str, str]:
    """
    Choose hedged credit for AUTO (intelligent switching).

    Returns (action, reason, strategy_mode).
    strategy_mode: ema_cross | cpr_sideways | cpr_trend | conflict | wait
    """
    aligned = str(cross.get("aligned") or "")
    ema_bull = aligned == "bull"
    ema_bear = aligned == "bear"
    bias = regime.day_bias

    cross_action = credit_action_for_cross(cross)
    if cross_action:
        if cross_action == "SELL_BEAR_CALL_SPREAD" and bias == "TRENDING_BULL":
            return (
                None,
                "AUTO: Bearish EMA cross conflicts with bullish CPR trend — no credit.",
                "conflict",
            )
        if cross_action == "SELL_BULL_PUT_SPREAD" and bias == "TRENDING_BEAR":
            return (
                None,
                "AUTO: Bullish EMA cross conflicts with bearish CPR trend — no credit.",
                "conflict",
            )
        label = "bullish" if cross_action == "SELL_BULL_PUT_SPREAD" else "bearish"
        return (
            cross_action,
            (
                f"AUTO [EMA cross]: {ema_fast}/{ema_slow} {label} cross on spot. "
                f"{regime.note}"
            ),
            "ema_cross",
        )

    if bias == "SIDEWAYS":
        if ema_bull or ema_bear:
            return (
                None,
                (
                    f"AUTO: Sideways CPR but EMA {aligned} — "
                    f"wait for cross or range (no iron condor vs trend)."
                ),
                "wait",
            )
        return (
            "SELL_IRON_CONDOR",
            f"AUTO [range]: Sideways CPR — iron condor (EMA {aligned or 'flat'}). {regime.note}",
            "cpr_sideways",
        )

    if bias == "TRENDING_BULL" and ema_bull:
        return (
            "SELL_BULL_PUT_SPREAD",
            (
                f"AUTO [trend]: Bullish CPR + EMA {ema_fast}/{ema_slow} aligned — "
                "bull put spread."
            ),
            "cpr_trend",
        )
    if bias == "TRENDING_BEAR" and ema_bear:
        return (
            "SELL_BEAR_CALL_SPREAD",
            (
                f"AUTO [trend]: Bearish CPR + EMA {ema_fast}/{ema_slow} aligned — "
                "bear call spread."
            ),
            "cpr_trend",
        )

    if bias in ("TRENDING_BULL", "TRENDING_BEAR"):
        return (
            None,
            (
                f"AUTO: CPR {bias} but EMA {aligned or 'flat'} not aligned — "
                "no credit (buy rules may apply)."
            ),
            "conflict",
        )

    return (
        None,
        f"AUTO: CPR {bias} — no credit setup this scan.",
        "wait",
    )
