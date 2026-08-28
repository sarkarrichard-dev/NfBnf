"""
Signal for the directional index-futures strategy.

Trend (15m bars):  long only when CPR bias, EMA, and Supertrend all agree up;
                   short only when all three agree down; otherwise flat.
Entry (5m bars):   in the trend direction, on a reclaim of the 5m EMA that is
                   not already over-extended.
Exit:              15m trend flips, hard stop, trailing stop, or square-off.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from index_ai.strategies.futures.config import FuturesConfig
from index_ai.strategies.strategy import previous_day_cpr
from index_ai.strategies.supertrend import compute_supertrend

FLAT, LONG, SHORT = 0, 1, -1


@dataclass(frozen=True)
class TrendRead:
    direction: int          # FLAT / LONG / SHORT
    price: float
    cpr_bias: int
    ema_bias: int
    st_bias: int
    st_line: float
    reason: str


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def trend_series(full15: pd.DataFrame, prev_day: pd.DataFrame, cfg: FuturesConfig) -> pd.Series:
    """Vectorised per-15m-bar trend direction (FLAT/LONG/SHORT), indexed like ``full15``.

    Computed once per session instead of re-deriving the whole thing on every
    5-minute bar.
    """
    df = full15.reset_index(drop=True).copy()
    ef = _ema(df["close"], cfg.trend_ema_fast)
    es = _ema(df["close"], cfg.trend_ema_slow)
    st = compute_supertrend(df, period=cfg.trend_st_period, multiplier=cfg.trend_st_mult)
    try:
        _, bc, tc = previous_day_cpr(prev_day)
    except Exception:
        bc = tc = float(df["close"].iloc[0])
    price = df["close"]
    cpr = pd.Series(0, index=df.index)
    cpr[price > tc] = 1
    cpr[price < bc] = -1
    ema_b = (ef > es).map({True: 1, False: -1})
    st_b = st["supertrend_direction"].astype(int)
    direction = pd.Series(FLAT, index=df.index)
    direction[(cpr >= 0) & (ema_b == 1) & (st_b == 1)] = LONG
    direction[(cpr <= 0) & (ema_b == -1) & (st_b == -1)] = SHORT
    return direction


def trend_read(bars15: pd.DataFrame, prev_day: pd.DataFrame, cfg: FuturesConfig) -> TrendRead:
    if len(bars15) < cfg.trend_min_bars:
        return TrendRead(FLAT, 0.0, 0, 0, 0, 0.0, "not enough 15m bars")

    df = bars15.copy()
    df["ema_fast"] = _ema(df["close"], cfg.trend_ema_fast)
    df["ema_slow"] = _ema(df["close"], cfg.trend_ema_slow)
    st = compute_supertrend(df, period=cfg.trend_st_period, multiplier=cfg.trend_st_mult)
    row = st.iloc[-1]
    price = float(row["close"])

    try:
        _, bc, tc = previous_day_cpr(prev_day)
    except Exception:
        bc, tc = price, price
    cpr_bias = 1 if price > tc else -1 if price < bc else 0
    ema_bias = 1 if row["ema_fast"] > row["ema_slow"] else -1
    st_bias = int(row["supertrend_direction"])

    if cpr_bias >= 0 and ema_bias == 1 and st_bias == 1:
        direction = LONG
    elif cpr_bias <= 0 and ema_bias == -1 and st_bias == -1:
        direction = SHORT
    else:
        direction = FLAT

    reason = f"CPR {cpr_bias:+d} / EMA {ema_bias:+d} / ST {st_bias:+d}"
    return TrendRead(direction, price, cpr_bias, ema_bias, st_bias, float(row["supertrend"]), reason)


def entry_trigger(bars5: pd.DataFrame, direction: int, cfg: FuturesConfig) -> tuple[bool, str]:
    """True on a fresh reclaim of the 5m EMA in ``direction`` that is not over-extended."""
    if direction == FLAT or len(bars5) < cfg.entry_min_bars:
        return False, "no trend / not enough 5m bars"

    ema = _ema(bars5["close"], cfg.entry_ema)
    prev_c, cur_c = float(bars5["close"].iloc[-2]), float(bars5["close"].iloc[-1])
    prev_e, cur_e = float(ema.iloc[-2]), float(ema.iloc[-1])

    extension = abs(cur_c - cur_e) / max(cur_e, 1.0) * 100.0
    if extension > cfg.max_extension_pct:
        return False, f"5m close {extension:.2f}% off the EMA — too extended"

    if direction == LONG and prev_c <= prev_e and cur_c > cur_e:
        return True, "5m reclaimed the EMA (long)"
    if direction == SHORT and prev_c >= prev_e and cur_c < cur_e:
        return True, "5m lost the EMA (short)"
    return False, "no 5m EMA cross this bar"
