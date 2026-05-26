"""CPR width / virgin / day-bias — sideways vs trending (classic CPR playbook)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from index_ai.strategy import previous_day_cpr
from index_ai.strategy_params import STRATEGY_PARAMS


@dataclass(frozen=True)
class CprRegime:
    pivot: float
    bc: float
    tc: float
    width: float
    width_pct: float
    width_class: str
    cpr_type: str
    virgin_cpr: bool
    price_position: str
    day_bias: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _width_class(width_pct: float) -> str:
    if width_pct <= STRATEGY_PARAMS.cpr_narrow_width_pct:
        return "NARROW"
    if width_pct >= STRATEGY_PARAMS.cpr_wide_width_pct:
        return "WIDE"
    return "NORMAL"


def _cpr_type(previous_day: pd.DataFrame) -> str:
    high = float(previous_day["high"].max())
    low = float(previous_day["low"].min())
    close = float(previous_day["close"].iloc[-1])
    if close > high:
        return "BULLISH"
    if close < low:
        return "BEARISH"
    return "SIDEWAYS"


def _price_position(price: float, bc: float, tc: float) -> str:
    if price > tc:
        return "above_cpr"
    if price < bc:
        return "below_cpr"
    return "inside_cpr"


def _day_bias(
    *,
    width_class: str,
    cpr_type: str,
    price_position: str,
    virgin: bool,
    ema_bull: bool,
    ema_bear: bool,
) -> str:
    if width_class == "WIDE" or price_position == "inside_cpr":
        return "SIDEWAYS"
    if width_class == "NARROW":
        if price_position == "above_cpr" or (ema_bull and cpr_type in {"BULLISH", "SIDEWAYS"}):
            return "TRENDING_BULL"
        if price_position == "below_cpr" or (ema_bear and cpr_type in {"BEARISH", "SIDEWAYS"}):
            return "TRENDING_BEAR"
    if price_position == "above_cpr" and ema_bull:
        return "TRENDING_BULL"
    if price_position == "below_cpr" and ema_bear:
        return "TRENDING_BEAR"
    if virgin and price_position == "above_cpr":
        return "TRENDING_BULL"
    if virgin and price_position == "below_cpr":
        return "TRENDING_BEAR"
    return "MIXED"


def analyze_cpr_regime(
    today: pd.DataFrame,
    previous_day: pd.DataFrame,
    *,
    price: float | None = None,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
) -> CprRegime:
    pivot, bc, tc = previous_day_cpr(previous_day)
    width = max(tc - bc, 0.0)
    width_pct = (width / max(pivot, 1.0)) * 100.0
    wclass = _width_class(width_pct)
    ctype = _cpr_type(previous_day)

    row = today.iloc[-1]
    spot = float(price if price is not None else row["close"])
    e_fast = float(ema_fast if ema_fast is not None else spot)
    e_slow = float(ema_slow if ema_slow is not None else spot)
    ema_bull = e_fast > e_slow
    ema_bear = e_fast < e_slow

    session_low = float(today["low"].min())
    session_high = float(today["high"].max())
    virgin = not (session_high >= bc and session_low <= tc)
    pos = _price_position(spot, bc, tc)
    bias = _day_bias(
        width_class=wclass,
        cpr_type=ctype,
        price_position=pos,
        virgin=virgin,
        ema_bull=ema_bull,
        ema_bear=ema_bear,
    )

    notes: list[str] = []
    if wclass == "WIDE":
        notes.append(f"Wide CPR ({width_pct:.2f}% of pivot) — range / sideways bias.")
    elif wclass == "NARROW":
        notes.append(f"Narrow CPR ({width_pct:.2f}%) — breakout / trending bias.")
    else:
        notes.append(f"CPR width {width_pct:.2f}% — normal.")
    if virgin:
        notes.append("Virgin CPR (today has not traded through BC–TC).")
    notes.append(f"Price {pos.replace('_', ' ')}; prior session CPR type {ctype.lower()}.")

    structure_hint = {
        "SIDEWAYS": "IRON_CONDOR",
        "TRENDING_BULL": "BULL_PUT_SPREAD",
        "TRENDING_BEAR": "BEAR_CALL_SPREAD",
        "MIXED": "",
    }.get(bias, "")

    if structure_hint:
        notes.append(f"Suggested hedged credit: {structure_hint.replace('_', ' ').title()}.")

    return CprRegime(
        pivot=pivot,
        bc=bc,
        tc=tc,
        width=round(width, 2),
        width_pct=round(width_pct, 4),
        width_class=wclass,
        cpr_type=ctype,
        virgin_cpr=virgin,
        price_position=pos,
        day_bias=bias,
        note=" ".join(notes),
    )
