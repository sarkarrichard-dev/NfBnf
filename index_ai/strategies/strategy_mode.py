"""Per-scan strategy mode selection for STRATEGY_STYLE=AUTO."""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.candlestick_sr import intraday_candle_trend
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.ema_cross import analyze_ema_cross, credit_action_for_cross
from index_ai.strategies.strategy_params import StrategyParams, get_strategy_params
from index_ai.strategies.supertrend import supertrend_snapshot


def summarize_trend15(frame15: pd.DataFrame | None, params: StrategyParams) -> dict[str, Any]:
    """15-minute trend + swing S/R context for the option-sell lanes.

    ``direction`` is decisive (+1 / -1) only when the 15m EMA alignment and the
    15m Supertrend agree, and the candle structure does not contradict it;
    otherwise 0. ``swing_high`` / ``swing_low`` bound the last N closed 15m bars —
    the levels a credit should not be sold straight through.
    """
    empty = {
        "direction": 0,
        "structure": "RANGE",
        "swing_high": 0.0,
        "swing_low": 0.0,
        "ready": False,
    }
    if frame15 is None or len(frame15) < params.ema_slow_period + 2:
        return empty
    cross = analyze_ema_cross(frame15, fast=params.ema_fast_period, slow=params.ema_slow_period)
    st = supertrend_snapshot(
        frame15, period=params.supertrend_period, multiplier=params.supertrend_multiplier
    )
    aligned = str(cross.get("aligned") or "")
    ema_dir = 1 if aligned == "bull" else -1 if aligned == "bear" else 0
    st_dir = int(st.get("direction") or 0)
    structure = intraday_candle_trend(frame15, lookback=15)
    direction = ema_dir if ema_dir != 0 and ema_dir == st_dir else 0
    if direction == 1 and structure == "DOWN":
        direction = 0
    elif direction == -1 and structure == "UP":
        direction = 0
    # swing S/R from *today's* 15m bars — the levels a live move just broke; falls
    # back to the whole frame only when today is still too thin.
    n_swing = max(2, params.sell_trend15_swing_lookback)
    today = frame15
    if "datetime" in frame15.columns:
        d = pd.to_datetime(frame15["datetime"]).dt.date
        today = frame15[d == d.iloc[-1]]
    swing_src = today if len(today) >= 2 else frame15
    tail = swing_src.tail(n_swing)
    return {
        "direction": direction,
        "structure": structure,
        "swing_high": float(tail["high"].astype(float).max()),
        "swing_low": float(tail["low"].astype(float).min()),
        "ready": True,
    }


def _volume_wait_reason(stats: dict[str, Any], *, min_ratio: float) -> str:
    return (
        f"AUTO: 5m bar volume {stats.get('last_bar_volume', 0):,} is "
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
    trend15: dict[str, Any] | None = None,
) -> tuple[str | None, str, str]:
    """
    Choose hedged credit for AUTO (intelligent switching).

    Uses CPR regime + 5m EMA cross/alignment + 5m bar volume vs recent average.
    ``trend15`` (from ``summarize_trend15``) is the 15m trend read — when given,
    every path requires it to be ready and not opposed (see ``_trend15_blocks``),
    not just the trend-override path.
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
    # a flickering EMA let a bull-put spread through (BANKNIFTY, 2026-09-08,
    # −₹580). A bearish credit needs the tape not to be UP, and vice versa.
    # ``frame`` is the 5m setup frame, so lookback=15 is 75 min of structure.
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
                "AUTO: bull-put credit blocked — 5m structure is DOWN; "
                "the day is selling off, don't sell puts into it."
            )
        if action == "SELL_BEAR_CALL_SPREAD" and _tape == "UP":
            return (
                "AUTO: bear-call credit blocked — 5m structure is UP; "
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
                    f" 5m vol {stats['last_bar_volume']:,} "
                    f"({stats['ratio']:.2f}× avg {stats['avg_bar_volume']:,})."
                )
            return action, f"{reason}{vol_note}", mode
        return None, _volume_wait_reason(stats, min_ratio=params.credit_min_volume_ratio), "wait"

    def _trend15_blocks(direction: int) -> str | None:
        """Reason string if the 15m read is not a real confirmation for
        ``direction`` (+1 a bull-put credit / -1 a bear-call credit), else
        None. Covers two cases the pre-2026-09-15 code let straight through:
        no 15m bars have closed yet (too early in the session for a real
        read — ``ready`` is False), or enough bars exist and the 15m trend
        actively disagrees. Only the trend-override path below checked this;
        the CPR+EMA-agree and fresh-cross paths did not, so BANKNIFTY and
        SENSEX both sold a bull-put spread 11 minutes after the 2026-09-15
        open on yesterday's CPR bias + two brand-new 5m candles alone — losing
        ~₹3,268 combined when the day went on to decline all session. Skipped
        entirely when the caller never passed a ``trend15`` read at all, or
        when the operator has turned ``SELL_REQUIRE_TREND15`` off."""
        if trend15 is None or not params.sell_require_trend15:
            return None
        if not trend15.get("ready"):
            return "AUTO: too early in the session for a confirmed 15m trend read — wait."
        d15 = int(trend15.get("direction") or 0)
        if direction > 0 and d15 < 0:
            return "AUTO: 15m trend is bearish — wait for it to agree before selling puts."
        if direction < 0 and d15 > 0:
            return "AUTO: 15m trend is bullish — wait for it to agree before selling calls."
        return None

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
        block = _trend15_blocks(1 if cross_action == "SELL_BULL_PUT_SPREAD" else -1)
        if block:
            return None, block, "wait"
        label = "bullish" if cross_action == "SELL_BULL_PUT_SPREAD" else "bearish"
        return _gate_volume(
            cross_action,
            (f"AUTO [EMA cross]: {ema_fast}/{ema_slow} {label} cross on 5m spot. {regime.note}"),
            "ema_cross",
        )

    if bias == "TRENDING_BULL" and ema_bull:
        block = _trend15_blocks(1)
        if block:
            return None, block, "wait"
        return _gate_volume(
            "SELL_BULL_PUT_SPREAD",
            (
                f"AUTO [trend]: Bullish CPR + EMA {ema_fast}/{ema_slow} aligned on 5m — "
                "bull put spread."
            ),
            "cpr_trend",
        )
    if bias == "TRENDING_BEAR" and ema_bear:
        block = _trend15_blocks(-1)
        if block:
            return None, block, "wait"
        return _gate_volume(
            "SELL_BEAR_CALL_SPREAD",
            (
                f"AUTO [trend]: Bearish CPR + EMA {ema_fast}/{ema_slow} aligned on 5m — "
                "bear call spread."
            ),
            "cpr_trend",
        )

    # Trend override — a strong, confirmed intraday trend overrides a conflicting
    # or flat daily CPR bias. Needs 5m EMA alignment + Supertrend + candle
    # structure to agree, and — when a 15m read is supplied — _trend15_blocks to
    # actually confirm it (ready + not opposing), not just fail to oppose. This
    # branch is self-sufficient rather than counting on the caller
    # (sell_strategy.py's own _trend15_block) to catch what a raw ``d15``
    # comparison would miss when the 15m read isn't ready yet.
    st_dir, trend = _st_dir, _tape
    strong_down = ema_bear and st_dir == -1 and trend == "DOWN"
    strong_up = ema_bull and st_dir == 1 and trend == "UP"
    if params.sell_allow_trend_override:
        if strong_down and bias in ("TRENDING_BULL", "SIDEWAYS"):
            block = _trend15_blocks(-1)
            if block:
                return None, block, "wait"
            return _gate_volume(
                "SELL_BEAR_CALL_SPREAD",
                (
                    f"AUTO [trend-override]: CPR {bias.lower()} but 5m EMA bear + "
                    "Supertrend down + candles DOWN — confirmed break, bear call spread."
                ),
                "cpr_trend_override",
            )
        if strong_up and bias in ("TRENDING_BEAR", "SIDEWAYS"):
            block = _trend15_blocks(1)
            if block:
                return None, block, "wait"
            return _gate_volume(
                "SELL_BULL_PUT_SPREAD",
                (
                    f"AUTO [trend-override]: CPR {bias.lower()} but 5m EMA bull + "
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
