"""Per-scan strategy mode selection for STRATEGY_STYLE=AUTO."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.ema_cross import credit_action_for_cross
from index_ai.strategies.strategy_params import get_strategy_params


def _volume_wait_reason(stats: dict[str, Any], *, min_ratio: float) -> str:
    return (
        f"AUTO: 1m bar volume {stats.get('last_bar_volume', 0):,} is "
        f"{float(stats.get('ratio') or 0):.2f}× recent avg "
        f"(need ≥{min_ratio:.2f}×) — wait for participation."
    )


def pick_auto_credit(
    regime: CprRegime,
    cross: dict[str, Any],
    *,
    ema_fast: int,
    ema_slow: int,
    frame: pd.DataFrame | None = None,
) -> tuple[str | None, str, str]:
    """
    Choose hedged credit for AUTO (intelligent switching).

    Uses CPR regime + 1m EMA cross/alignment + bar volume vs recent average.
    Returns (action, reason, strategy_mode).
    """
    params = get_strategy_params()
    aligned = str(cross.get("aligned") or "")
    ema_bull = aligned == "bull"
    ema_bear = aligned == "bear"
    bias = regime.day_bias

    def _gate_volume(action: str | None, reason: str, mode: str) -> tuple[str | None, str, str]:
        if not action:
            return action, reason, mode
        ok, stats = volume_confirms(
            frame,
            min_ratio=params.credit_min_volume_ratio,
            lookback=params.credit_volume_lookback_bars,
        )
        if ok:
            vol_note = ""
            if stats.get("ready"):
                vol_note = (
                    f" 1m vol {stats['last_bar_volume']:,} "
                    f"({stats['ratio']:.2f}× avg {stats['avg_bar_volume']:,})."
                )
            return action, f"{reason}{vol_note}", mode
        return None, _volume_wait_reason(stats, min_ratio=params.credit_min_volume_ratio), "wait"

    cross_action = credit_action_for_cross(cross)
    if cross_action:
        if bias in {"SIDEWAYS", "MIXED"}:
            return (
                None,
                (
                    f"AUTO: Fresh {ema_fast}/{ema_slow} EMA cross while CPR is {bias.lower()} "
                    "— wait for a confirmed break instead of selling into a possible whipsaw."
                ),
                "wait",
            )
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
        return _gate_volume(
            cross_action,
            (
                f"AUTO [EMA cross]: {ema_fast}/{ema_slow} {label} cross on 1m spot. "
                f"{regime.note}"
            ),
            "ema_cross",
        )

    if bias == "SIDEWAYS":
        # Directional-only credit policy: no range selling (iron condor). A sideways
        # CPR is a no-trade for the sell lane — wait for a directional break.
        return (
            None,
            f"AUTO: Sideways CPR — directional-only credit policy, no range sell. {regime.note}",
            "wait",
        )

    if bias == "TRENDING_BULL" and ema_bull:
        return _gate_volume(
            "SELL_BULL_PUT_SPREAD",
            (
                f"AUTO [trend]: Bullish CPR + EMA {ema_fast}/{ema_slow} aligned on 1m — "
                "bull put spread."
            ),
            "cpr_trend",
        )
    if bias == "TRENDING_BEAR" and ema_bear:
        return _gate_volume(
            "SELL_BEAR_CALL_SPREAD",
            (
                f"AUTO [trend]: Bearish CPR + EMA {ema_fast}/{ema_slow} aligned on 1m — "
                "bear call spread."
            ),
            "cpr_trend",
        )

    if bias in ("TRENDING_BULL", "TRENDING_BEAR"):
        return (
            None,
            (
                f"AUTO: CPR {bias} but EMA {aligned or 'flat'} not aligned — "
                "no credit (trend buy rules may apply)."
            ),
            "conflict",
        )

    return (
        None,
        f"AUTO: CPR {bias} — no credit setup this scan.",
        "wait",
    )
