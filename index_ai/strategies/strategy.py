from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from index_ai.strategies.breakout import detect_breakout
from index_ai.instruments import IndexInstrument
from index_ai.strategies.strategy_params import StrategyParams, get_strategy_params
from index_ai.strategies.supertrend import supertrend_snapshot


@dataclass(frozen=True)
class StrategySignal:
    action: str
    reason: str
    confidence: float
    price: float
    pivot: float
    bc: float
    tc: float
    ema_fast: float
    ema_slow: float
    ema_cross: str = ""
    ema_aligned: str = ""
    strategy_mode: str = ""
    supertrend_direction: int = 0
    supertrend_stop: float = 0.0
    breakout_tag: str = ""
    cpr_width_pct: float = 0.0
    cpr_width_class: str = ""
    cpr_regime: str = ""
    cpr_virgin: bool = False
    recommended_structure: str = ""
    ema_spread_pct: float = 0.0
    volume_ratio: float = 1.0
    entry_quality: str = ""
    sr_source: str = ""  # "oi" (real option-chain walls) or "candle" (range guess) — buy lane only

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def copy_signal(signal: StrategySignal, **updates: Any) -> StrategySignal:
    data = asdict(signal)
    data.update(updates)
    return StrategySignal(**data)


def add_indicators(
    candles: pd.DataFrame,
    fast: int | None = None,
    slow: int | None = None,
) -> pd.DataFrame:
    params = get_strategy_params()
    f = int(fast if fast is not None else params.ema_fast_period)
    s = int(slow if slow is not None else params.ema_slow_period)
    df = candles.copy()
    df["ema_fast"] = df["close"].ewm(span=f, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=s, adjust=False).mean()
    return df


def previous_day_cpr(previous_day: pd.DataFrame) -> tuple[float, float, float]:
    high = float(previous_day["high"].max())
    low = float(previous_day["low"].min())
    close = float(previous_day["close"].iloc[-1])
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = 2.0 * pivot - bc
    return pivot, min(bc, tc), max(bc, tc)


def _ema_spread_pct(price: float, ema_fast: float, ema_slow: float) -> float:
    return abs(ema_fast - ema_slow) / max(abs(price), 1.0) * 100.0


def _confirmed_direction(df: pd.DataFrame, *, bars: int, cpr_level: float, bullish: bool) -> bool:
    lookback = max(1, int(bars))
    recent = df.tail(lookback)
    if len(recent) < lookback:
        return False
    if bullish:
        return bool(
            ((recent["close"] > cpr_level) & (recent["ema_fast"] > recent["ema_slow"])).all()
        )
    return bool(((recent["close"] < cpr_level) & (recent["ema_fast"] < recent["ema_slow"])).all())


def _no_trade_signal(
    *,
    reason: str,
    price: float,
    pivot: float,
    bc: float,
    tc: float,
    ema_fast: float,
    ema_slow: float,
    ema_spread_pct: float,
    entry_quality: str,
) -> StrategySignal:
    return StrategySignal(
        action="NO_TRADE",
        reason=reason,
        confidence=0.0,
        price=price,
        pivot=pivot,
        bc=bc,
        tc=tc,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_spread_pct=round(ema_spread_pct, 4),
        entry_quality=entry_quality,
    )


def cpr_ema_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    params: StrategyParams | None = None,
) -> StrategySignal:
    cfg = params or get_strategy_params()
    confirm_bars = max(1, int(cfg.entry_confirmation_bars))
    min_bars = max(cfg.ema_slow_period + 1, confirm_bars)
    if len(today) < min_bars:
        raise ValueError(f"Need at least {min_bars} intraday candles for EMA signal.")
    df = add_indicators(today, fast=cfg.ema_fast_period, slow=cfg.ema_slow_period)
    row = df.iloc[-1]
    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    spread_pct = _ema_spread_pct(price, ema_fast, ema_slow)
    pivot, bc, tc = previous_day_cpr(previous_day)
    min_spread = max(0.0, float(cfg.min_directional_ema_spread_pct))
    max_extension = max(0.0, float(cfg.max_cpr_entry_extension_pct))

    if price > tc and ema_fast > ema_slow:
        if spread_pct < min_spread:
            return _no_trade_signal(
                reason=(f"EMA spread {spread_pct:.3f}% is below quality gate {min_spread:.3f}%."),
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="weak_ema_spread",
            )
        if not _confirmed_direction(df, bars=confirm_bars, cpr_level=tc, bullish=True):
            return _no_trade_signal(
                reason=f"Waiting for {confirm_bars} confirmed closes above CPR top with EMA alignment.",
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="unconfirmed_cpr_break",
            )
        distance = min(1.0, abs(price - tc) / max(price * 0.004, 1.0))
        extension_pct = (price - tc) / max(abs(price), 1.0) * 100.0
        if max_extension and extension_pct > max_extension:
            return _no_trade_signal(
                reason=(
                    f"Long setup is extended {extension_pct:.2f}% above CPR top "
                    f"(gate {max_extension:.2f}%)."
                ),
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="late_extended_entry",
            )
        confidence = 0.55 + distance * 0.25
        return StrategySignal(
            action="BUY_CALL",
            reason="Price is above CPR top and EMA fast is above EMA slow.",
            confidence=round(confidence, 3),
            price=price,
            pivot=pivot,
            bc=bc,
            tc=tc,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_spread_pct=round(spread_pct, 4),
            entry_quality="confirmed_directional",
        )
    if price < bc and ema_fast < ema_slow:
        if spread_pct < min_spread:
            return _no_trade_signal(
                reason=(f"EMA spread {spread_pct:.3f}% is below quality gate {min_spread:.3f}%."),
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="weak_ema_spread",
            )
        if not _confirmed_direction(df, bars=confirm_bars, cpr_level=bc, bullish=False):
            return _no_trade_signal(
                reason=f"Waiting for {confirm_bars} confirmed closes below CPR bottom with EMA alignment.",
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="unconfirmed_cpr_break",
            )
        distance = min(1.0, abs(price - bc) / max(price * 0.004, 1.0))
        extension_pct = (bc - price) / max(abs(price), 1.0) * 100.0
        if max_extension and extension_pct > max_extension:
            return _no_trade_signal(
                reason=(
                    f"Short setup is extended {extension_pct:.2f}% below CPR bottom "
                    f"(gate {max_extension:.2f}%)."
                ),
                price=price,
                pivot=pivot,
                bc=bc,
                tc=tc,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_spread_pct=spread_pct,
                entry_quality="late_extended_entry",
            )
        confidence = 0.55 + distance * 0.25
        return StrategySignal(
            action="BUY_PUT",
            reason="Price is below CPR bottom and EMA fast is below EMA slow.",
            confidence=round(confidence, 3),
            price=price,
            pivot=pivot,
            bc=bc,
            tc=tc,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_spread_pct=round(spread_pct, 4),
            entry_quality="confirmed_directional",
        )
    return StrategySignal(
        action="NO_TRADE",
        reason="CPR and EMA are not aligned.",
        confidence=0.0,
        price=price,
        pivot=pivot,
        bc=bc,
        tc=tc,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_spread_pct=round(spread_pct, 4),
        entry_quality="not_aligned",
    )


def _chart_context(today: pd.DataFrame, params: StrategyParams) -> tuple[dict, dict]:
    st = supertrend_snapshot(
        today,
        period=params.supertrend_period,
        multiplier=params.supertrend_multiplier,
    )
    br = detect_breakout(today, lookback=params.breakout_lookback)
    return st, br


def _with_chart_fields(
    signal: StrategySignal,
    st: dict,
    br: dict,
    *,
    breakout_tag: str | None = None,
) -> StrategySignal:
    tag = breakout_tag if breakout_tag is not None else (br.get("breakout_tag") or "")
    return copy_signal(
        signal,
        supertrend_direction=int(st["direction"]) if st.get("ready") else 0,
        supertrend_stop=float(st["stop"]) if st.get("ready") else 0.0,
        breakout_tag=tag,
    )


def intraday_strategy_signal(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    params: StrategyParams | None = None,
) -> StrategySignal:
    """CPR+EMA core, filtered by Supertrend and Break Res / Break Sup (AK Roxx style)."""
    cfg = params or get_strategy_params()
    base = cpr_ema_signal(today, previous_day, params=cfg)
    st, br = _chart_context(today, cfg)

    if base.action == "NO_TRADE":
        return _with_chart_fields(base, st, br)

    if cfg.require_supertrend_align and st.get("ready"):
        if base.action == "BUY_CALL" and st["direction"] != 1:
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="CPR/EMA long but Supertrend is bearish.",
                    confidence=0.0,
                ),
                st,
                br,
            )
        if base.action == "BUY_PUT" and st["direction"] != -1:
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="CPR/EMA short but Supertrend is bullish.",
                    confidence=0.0,
                ),
                st,
                br,
            )

    if cfg.require_breakout_tag:
        if base.action == "BUY_CALL" and not br.get("break_res"):
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="Long setup without Break Res confirmation.",
                    confidence=0.0,
                ),
                st,
                br,
            )
        if base.action == "BUY_PUT" and not br.get("break_sup"):
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="Short setup without Break Sup confirmation.",
                    confidence=0.0,
                ),
                st,
                br,
            )

    reason = base.reason
    confidence = base.confidence
    tag = ""

    if base.action == "BUY_CALL":
        if br.get("break_sup"):
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="Break Sup against long — skipped.",
                    confidence=0.0,
                ),
                st,
                br,
                breakout_tag="BREAK_SUP",
            )
        if br.get("break_res"):
            reason = f"{reason} Break Res: close above {br['range_high']:.0f} range high."
            confidence = min(0.95, confidence + cfg.breakout_confidence_boost)
            tag = "BREAK_RES"
    elif base.action == "BUY_PUT":
        if br.get("break_res"):
            return _with_chart_fields(
                copy_signal(
                    base,
                    action="NO_TRADE",
                    reason="Break Res against short — skipped.",
                    confidence=0.0,
                ),
                st,
                br,
                breakout_tag="BREAK_RES",
            )
        if br.get("break_sup"):
            reason = f"{reason} Break Sup: close below {br['range_low']:.0f} range low."
            confidence = min(0.95, confidence + cfg.breakout_confidence_boost)
            tag = "BREAK_SUP"

    if st.get("ready"):
        trend = "bullish" if st["direction"] == 1 else "bearish"
        reason = f"{reason} Supertrend {trend} (stop {st['stop']:.0f})."

    return _with_chart_fields(
        copy_signal(
            base,
            reason=reason,
            confidence=round(confidence, 3),
        ),
        st,
        br,
        breakout_tag=tag,
    )


def nearest_strike(price: float, instrument: IndexInstrument) -> int:
    step = instrument.strike_step
    return int(round(price / step) * step)


def choose_option_from_chain(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    *,
    transaction_type: str = "BUY",
) -> dict[str, Any]:
    strike = nearest_strike(signal.price, instrument)
    side = "ce" if signal.action == "BUY_CALL" else "pe"
    option_rows = (chain.get("data") or {}).get("oc") or {}
    if not option_rows:
        raise RuntimeError("Dhan option chain returned no strikes.")
    selected_key = min(option_rows.keys(), key=lambda key: abs(float(key) - strike))
    selected = float(selected_key)
    row = option_rows.get(selected_key) or {}
    leg = row.get(side) or {}
    security_id = leg.get("security_id")
    if security_id is None:
        raise RuntimeError("Selected strike has no option security id.")
    return {
        "instrument": instrument.key,
        "option_type": "CALL" if side == "ce" else "PUT",
        "strike": selected,
        "security_id": int(security_id),
        "segment": instrument.option_segment,
        "ltp": leg.get("last_price"),
        "quantity": instrument.lot_size,
        "transaction_type": transaction_type.upper(),
    }
