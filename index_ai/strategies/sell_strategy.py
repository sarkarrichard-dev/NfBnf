"""Directional credit selling.

**2-leg directional only** (Richard, 2026-09-10): the sell lane emits *only*
``SELL_BULL_PUT_SPREAD`` / ``SELL_BEAR_CALL_SPREAD`` — one sold leg + one hedge,
for credit. No range selling (iron condor), no naked single-leg.

**Direction comes from the OI profile** (Richard's edge): the max call/put OI
strikes are the ceiling and floor, max pain is the bias. CPR is kept only for
target / stop pivots downstream. When the chain is missing *or* carries no
usable OI walls, the lane falls back to the CPR + EMA read
(``pick_auto_credit``). The OI path still re-applies ``pick_auto_credit``'s 5m
tape veto — a bull put credit is never sold into a selling-off day.

A **breakout that is still holding** (price beyond the prior 20-bar range right
now) is a *veto only* — the lane will not sell a spread that fights it. No
reversal-entry logic.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.options_oi import OptionOiContext
from index_ai.strategies.bar_volume import volume_confirms
from index_ai.strategies.candlestick_sr import intraday_candle_trend
from index_ai.strategies.cpr_regime import CprRegime
from index_ai.strategies.ema_cross import analyze_ema_cross
from index_ai.strategies.oi_credit import decide as oi_decide
from index_ai.strategies.strategy import StrategySignal, add_indicators
from index_ai.strategies.strategy_mode import pick_auto_credit
from index_ai.strategies.strategy_params import StrategyParams, get_strategy_params

_ALLOWED_SELL_ACTIONS = frozenset({"SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD"})
_BULL_CREDIT = "SELL_BULL_PUT_SPREAD"
_BEAR_CREDIT = "SELL_BEAR_CALL_SPREAD"


def _breakout_vetoes(action: str, frame: pd.DataFrame) -> str:
    """A breakout that is *currently holding* — price beyond the range of the
    bars *before* the break (last 3 bars excluded so a 1-3 bar break is measured
    against the pre-break range) — vetoes a credit spread that fights it."""
    if len(frame) < 24:
        return ""
    prior = frame.iloc[-24:-3]  # ~21 bars, ending 3 before now
    hi, lo = float(prior["high"].max()), float(prior["low"].min())
    last = float(frame["close"].iloc[-1])
    if action == _BEAR_CREDIT and last > hi:
        return f"price {last:.0f} above the prior range high {hi:.0f} — upside breakout holding"
    if action == _BULL_CREDIT and last < lo:
        return f"price {last:.0f} below the prior range low {lo:.0f} — breakdown holding"
    return ""


def _tape_opposes(action: str, frame: pd.DataFrame) -> str:
    """The 5m HH/HL-vs-LH/LL tape read — same veto ``pick_auto_credit`` applies,
    re-applied on the OI path so a bull put credit can't be sold into a
    selling-off day (the 2026-09-08 BANKNIFTY loss)."""
    tape = intraday_candle_trend(frame, lookback=15)
    if action == _BULL_CREDIT and tape == "DOWN":
        return "5m tape is DOWN — the day is selling off, don't sell puts into it"
    if action == _BEAR_CREDIT and tape == "UP":
        return "5m tape is UP — the day is rallying, don't sell calls into it"
    return ""


def _structure_for_bias(day_bias: str) -> str:
    return {
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


_BULL_SELL = {"SELL_BULL_PUT_SPREAD"}


def _trend15_block(
    action: str, price: float, trend15: dict[str, Any] | None, cfg: StrategyParams
) -> str | None:
    """Reason the 15m trend / swing S&R vetoes this credit, or None.

    A bullish credit (short puts) needs the 15m trend not to be down and price
    not to have broken the 15m swing low; mirror for a bearish credit.

    ``trend15 is None`` (no 15m read was even attempted — e.g. the 5m frame
    itself was too thin to run the sell lane) or the operator has
    ``sell_require_trend15`` off: no opinion, don't block. But once a real
    15m read was attempted and just isn't ready yet (too early in the session
    for a full 15m bar), that must block too, same as an opposing read would —
    2026-09-15: this is the check that runs for *every* sell path, including
    the OI-primary one that bypasses ``pick_auto_credit`` entirely, and
    "not ready" used to fall through as "no objection" here, which is exactly
    how BANKNIFTY and SENSEX could still have sold the wrong direction 11
    minutes after the open even with a decisive OI read (they didn't, only
    because OI happened to be unclear that morning too — but this path was
    never actually protected).
    """
    if trend15 is None or not cfg.sell_require_trend15:
        return None
    if not trend15.get("ready"):
        return "too early in the session for a confirmed 15m trend read."
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
    oi: OptionOiContext | None = None,
) -> StrategySignal:
    """Directional credit selling.

    Direction is picked from ``oi`` (the OI walls + max pain) when it is present
    and ``sell_oi_primary`` is set; otherwise from the CPR + EMA read
    (``pick_auto_credit``). ``trend15`` and the 5m volume gate are kept as
    secondary confirmations. A still-holding intraday breakout vetoes a spread
    that fights it.
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

    oi_usable = (
        oi is not None and oi.max_call_oi_strike is not None and oi.max_put_oi_strike is not None
    )

    def _cpr_read() -> tuple[str | None, str, str]:
        return pick_auto_credit(
            regime,
            cross,
            ema_fast=cfg.ema_fast_period,
            ema_slow=cfg.ema_slow_period,
            frame=df,
            trend15=trend15,
        )

    if cfg.sell_oi_primary and oi_usable:
        action, reason, oi_fallback_ok = oi_decide(oi, price, max_pain=oi.max_pain)
        mode = "oi_credit"
        if not action and not oi_fallback_ok:
            # hard veto (spot pinned to max pain) — don't try CPR either
            return StrategySignal(
                action="NO_TRADE",
                reason=f"OI sell: {reason}",
                confidence=0.0,
                strategy_mode="wait",
                **base,
            )
        if not action:
            # OI has no directional read (walls crossed / on a wall / mid-range)
            # — fall back to the CPR + EMA direction rather than sit out.
            action, cpr_reason, cpr_mode = _cpr_read()
            if not action:
                return StrategySignal(
                    action="NO_TRADE",
                    reason=cpr_reason or f"OI stood down ({reason}); no CPR setup either.",
                    confidence=0.0,
                    strategy_mode=cpr_mode or "wait",
                    **base,
                )
            # keep the real CPR mode so the trade is scored and bucketed like a
            # native CPR credit (its provenance is in the reason string).
            reason = f"OI unclear ({reason}) → CPR: {cpr_reason}"
            mode = cpr_mode or "cpr_fallback"
        else:
            # the OI path bypasses pick_auto_credit — re-apply its 5m tape veto
            # here, the one guard that stops selling into a wrong-way move.
            opp = _tape_opposes(action, df)
            if opp:
                return StrategySignal(
                    action="NO_TRADE",
                    reason=f"OI sell blocked — {opp}.",
                    confidence=0.0,
                    strategy_mode="conflict",
                    **base,
                )
    else:
        action, reason, mode = _cpr_read()
        if not action:
            return StrategySignal(
                action="NO_TRADE",
                reason=reason or f"No CPR sell setup ({regime.day_bias}).",
                confidence=0.0,
                strategy_mode=mode or "wait",
                **base,
            )

    veto = _breakout_vetoes(action, df)
    if veto:
        return StrategySignal(
            action="NO_TRADE",
            reason=f"Sell vetoed — {veto}.",
            confidence=0.0,
            strategy_mode="wait",
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

    # hard stop: the sell lane is 2-leg directional only. pick_auto_credit is
    # already directional-only, so this only fires if something upstream changes.
    if action not in _ALLOWED_SELL_ACTIONS:
        return StrategySignal(
            action="NO_TRADE",
            reason=f"Sell lane is 2-leg directional only — {action} is not allowed.",
            confidence=0.0,
            strategy_mode="wait",
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
