# ============================================================================
# PARKED SNAPSHOT — not imported by anything yet.
#
# Copied from index_ai/strategies/ichimoku.py on 2026-09-03 so the crypto lane
# has its Ichimoku component in one place. The CANONICAL, live copy is still
# index_ai/strategies/ichimoku.py — index_ai/backtest.py imports it for the
# EXIT_BUY_ON_CLOUD_REENTRY branch, so do not delete that one.
#
# When the crypto lane is actually built, decide deliberately: import the
# shared module, or fork it here with crypto-tuned periods. Do not let both
# drift silently.
#
# Settings note: the index side uses the classic 9/26/52 (strategy_params.
# ichimoku_*). Crypto is 24/7 with no session gaps, so the period and
# displacement choice is an open question to settle with a backtest before
# this goes anywhere near an order.
# ============================================================================

"""
Ichimoku Kinko Hyo — conversion/base lines and the forward-displaced Kumo (cloud).

Used as an optional *exit* overlay for the long-premium (buy) lane: once price
loses the cloud it has usually lost the trend that justified buying the option.
Inspired by the Renko + Ichimoku swing method, adapted to time-based intraday
spot candles.

All series are non-repainting: the cloud shown at bar ``i`` is built from data
at bar ``i - displacement`` (that is how Ichimoku projects Senkou A/B forward),
so reading ``senkou_a`` / ``senkou_b`` on the latest closed bar never peeks
ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

CONVERSION_PERIOD = 9
BASE_PERIOD = 26
SPAN_B_PERIOD = 52
DISPLACEMENT = 26

_LongRef = Literal["cloud_top", "kijun"]
_ShortRef = Literal["cloud_bottom", "kijun"]


def _midpoint(high: pd.Series, low: pd.Series, window: int) -> pd.Series:
    return (high.rolling(window).max() + low.rolling(window).min()) / 2.0


def compute_ichimoku(
    candles: pd.DataFrame,
    *,
    conversion: int = CONVERSION_PERIOD,
    base: int = BASE_PERIOD,
    span_b: int = SPAN_B_PERIOD,
    displacement: int = DISPLACEMENT,
) -> pd.DataFrame:
    """Return a copy of ``candles`` with tenkan / kijun / senkou_a / senkou_b / cloud bounds.

    ``senkou_a`` / ``senkou_b`` are already displaced forward by ``displacement``
    bars, so row ``i`` carries the cloud that is in force at bar ``i``.
    """
    df = candles.copy()
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    tenkan = _midpoint(high, low, conversion)
    kijun = _midpoint(high, low, base)
    senkou_a_raw = (tenkan + kijun) / 2.0
    senkou_b_raw = _midpoint(high, low, span_b)

    df["tenkan"] = tenkan
    df["kijun"] = kijun
    df["senkou_a"] = senkou_a_raw.shift(displacement)
    df["senkou_b"] = senkou_b_raw.shift(displacement)
    df["cloud_top"] = df[["senkou_a", "senkou_b"]].max(axis=1)
    df["cloud_bottom"] = df[["senkou_a", "senkou_b"]].min(axis=1)
    return df


@dataclass(frozen=True)
class IchimokuSnapshot:
    ready: bool
    price: float
    tenkan: float
    kijun: float
    cloud_top: float
    cloud_bottom: float
    position: Literal["above", "inside", "below", "unknown"]


def ichimoku_snapshot(
    candles: pd.DataFrame,
    *,
    conversion: int = CONVERSION_PERIOD,
    base: int = BASE_PERIOD,
    span_b: int = SPAN_B_PERIOD,
    displacement: int = DISPLACEMENT,
) -> IchimokuSnapshot:
    price = float(candles["close"].iloc[-1]) if len(candles) else 0.0
    need = span_b + displacement + 1
    if len(candles) < need:
        return IchimokuSnapshot(False, price, 0.0, 0.0, 0.0, 0.0, "unknown")

    frame = compute_ichimoku(
        candles, conversion=conversion, base=base, span_b=span_b, displacement=displacement
    )
    row = frame.iloc[-1]
    top = float(row["cloud_top"])
    bottom = float(row["cloud_bottom"])
    if pd.isna(top) or pd.isna(bottom):
        return IchimokuSnapshot(False, price, 0.0, 0.0, 0.0, 0.0, "unknown")

    if price > top:
        position: Literal["above", "inside", "below"] = "above"
    elif price < bottom:
        position = "below"
    else:
        position = "inside"

    return IchimokuSnapshot(
        ready=True,
        price=price,
        tenkan=float(row["tenkan"]),
        kijun=float(row["kijun"]),
        cloud_top=top,
        cloud_bottom=bottom,
        position=position,
    )


def cloud_reentry_exit(
    entry_direction: int,
    candles: pd.DataFrame,
    *,
    long_ref: _LongRef = "cloud_top",
    short_ref: _ShortRef = "kijun",
    conversion: int = CONVERSION_PERIOD,
    base: int = BASE_PERIOD,
    span_b: int = SPAN_B_PERIOD,
    displacement: int = DISPLACEMENT,
) -> tuple[bool, str | None]:
    """Trailing exit for a directional long-premium trade.

    ``entry_direction``: +1 for a bullish position (BUY_CALL), -1 for bearish
    (BUY_PUT).

    Dual-system exit (asymmetric by default, matching the source method):
      * long  → exit when the bar closes at/below the cloud top ("lost the cloud")
      * short → exit when the bar closes at/above the Kijun (fast follow)

    Returns ``(should_exit, reason)``. Never exits while the cloud is not ready.
    """
    if entry_direction == 0:
        return False, None

    snap = ichimoku_snapshot(
        candles, conversion=conversion, base=base, span_b=span_b, displacement=displacement
    )
    if not snap.ready:
        return False, None

    if entry_direction > 0:
        level = snap.cloud_top if long_ref == "cloud_top" else snap.kijun
        if snap.price <= level:
            return True, f"Close {snap.price:g} back into cloud (top {level:g})."
        return False, None

    level = snap.cloud_bottom if short_ref == "cloud_bottom" else snap.kijun
    if snap.price >= level:
        ref = "cloud bottom" if short_ref == "cloud_bottom" else "Kijun"
        return True, f"Close {snap.price:g} back above {ref} {level:g}."
    return False, None
