"""Per-scan strategy mode selection for STRATEGY_STYLE=AUTO."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.candlestick_sr import intraday_candle_trend
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.ema_cross import credit_action_for_cross
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.strategies.supertrend import supertrend_snapshot


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

    # The intraday tape, independent of the day-old CPR bias. Used to VETO a
    # credit that fights the way the day is actually moving — CPR read
    # TRENDING_BULL off a prior-day pivot while price bled down all session, and
    # a flickering 1m EMA let a bull-put spread through (BANKNIFTY, 2026-09-08,
    # −₹580). A bearish credit needs the tape not to be UP, and vice versa.
    _st = (
        supertrend_snapshot(
            frame, period=params.supertrend_period, multiplier=params.supertrend_multiplier
        )
        if frame is not None
        else {"direction": 0}
    )
    _st_dir = int(_st.get("direction") or 0)
    _tape = intraday_candle_trend(frame, lookback=15) if frame is not None else "RANGE"

    def _tape_veto(action: str | None) -> str | None:
        """Reason string if the intraday tape opposes the credit direction, else
        None. ``intraday_candle_trend`` is already the deliberately-smoothed
        HH/HL-vs-LH/LL read; the cost of a false veto is one marginal credit
        skipped (every tested credit config is net-negative — strategy-findings),
        the cost of a false pass is real rupees the wrong way, so it vetoes on
        the structure alone rather than waiting for Supertrend to also agree."""
        if action == "SELL_BULL_PUT_SPREAD" and _tape == "DOWN":
            return (
                "AUTO: bull-put credit blocked — 1m structure is DOWN; "
                "the day is selling off, don't sell puts into it."
            )
        if action == "SELL_BEAR_CALL_SPREAD" and _tape == "UP":
            return (
                "AUTO: bear-call credit blocked — 1m structure is UP; "
                "the day is rallying, don't sell calls into it."
            )
        return None

    def _gate_volume(action: str | None, reason: str, mode: str) -> tuple[str | None, str, str]:
        if not action:
            return action, reason, mode
        veto = _tape_veto(action)
        if veto:
            return None, veto, "conflict"
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
            (f"AUTO [EMA cross]: {ema_fast}/{ema_slow} {label} cross on 1m spot. {regime.note}"),
            "ema_cross",
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

    # Trend override — a strong, confirmed intraday trend overrides a conflicting
    # or flat daily CPR bias. Needs all three of EMA alignment + Supertrend +
    # candle structure to agree (plus the volume gate below). It trades the
    # confirmed break, never a range: a merely-flat SIDEWAYS day produces nothing.
    st_dir, trend = _st_dir, _tape
    strong_down = ema_bear and st_dir == -1 and trend == "DOWN"
    strong_up = ema_bull and st_dir == 1 and trend == "UP"
    if params.sell_allow_trend_override:
        if strong_down and bias in ("TRENDING_BULL", "SIDEWAYS"):
            return _gate_volume(
                "SELL_BEAR_CALL_SPREAD",
                (
                    f"AUTO [trend-override]: CPR {bias.lower()} but 1m EMA bear + "
                    "Supertrend down + candles DOWN — confirmed break, bear call spread."
                ),
                "cpr_trend_override",
            )
        if strong_up and bias in ("TRENDING_BEAR", "SIDEWAYS"):
            return _gate_volume(
                "SELL_BULL_PUT_SPREAD",
                (
                    f"AUTO [trend-override]: CPR {bias.lower()} but 1m EMA bull + "
                    "Supertrend up + candles UP — confirmed break, bull put spread."
                ),
                "cpr_trend_override",
            )

    if bias == "SIDEWAYS":
        # Directional-only credit policy: no range selling (iron condor). A sideways
        # CPR with no confirmed trend is a no-trade — wait for a directional break.
        return (
            None,
            f"AUTO: Sideways CPR — directional-only credit policy, no range sell. {regime.note}",
            "wait",
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
