"""
Signal for the CPR + EMA option-buying strategy (spec Section 1-3).

Trend/context is the previous day's CPR; entries are 5m-close breakouts of the
CPR top/bottom line that are confirmed by EMA-9/21 alignment and above-average
volume. WIDE-CPR days need the breakout to hold for N consecutive closes; a
level that has already whipsawed in the last few candles is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from index_ai.strategies.options_cpr.config import OptionsCprConfig
from index_ai.strategies.strategy import previous_day_cpr


@dataclass(frozen=True)
class CprContext:
    pivot: float
    bc: float
    tc: float
    width_pct: float
    width_class: str            # "NARROW" | "NORMAL" | "WIDE"


def cpr_context(prev_day: pd.DataFrame, cfg: OptionsCprConfig) -> CprContext:
    pivot, bc, tc = previous_day_cpr(prev_day)
    width_pct = (tc - bc) / max(pivot, 1.0) * 100.0
    if width_pct <= cfg.cpr_narrow_threshold_pct:
        cls = "NARROW"
    elif width_pct >= cfg.cpr_wide_threshold_pct:
        cls = "WIDE"
    else:
        cls = "NORMAL"
    return CprContext(pivot, bc, tc, round(width_pct, 4), cls)


def add_indicators(df: pd.DataFrame, cfg: OptionsCprConfig) -> pd.DataFrame:
    """EMA-fast/slow, ATR, rolling average volume. ``df`` should already carry a
    prev-day tail for warm-up."""
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(cfg.atr_period, min_periods=1).mean()
    out["vol_avg"] = out["volume"].rolling(cfg.volume_lookback, min_periods=1).mean()
    return out


def _aligned(row: pd.Series, level: float, bullish: bool) -> bool:
    c, ef, es = float(row["close"]), float(row["ema_fast"]), float(row["ema_slow"])
    if bullish:
        return c > level and c > ef and c > es and ef > es
    return c < level and c < ef and c < es and ef < es


def _recent_whipsaw(closes: list[float], level: float) -> bool:
    """True if the (pre-current) window already traded both sides of ``level``."""
    prior = closes[:-1]
    return any(c > level for c in prior) and any(c < level for c in prior)


def evaluate_entry(
    df: pd.DataFrame, i: int, cpr: CprContext, cfg: OptionsCprConfig
) -> tuple[str | None, str]:
    """``df`` = indicator frame (prev-day tail + today). ``i`` = current bar index.

    Returns ("CE"|"PE"|None, reason).
    """
    if i < cfg.warmup_bars:
        return None, "warming up"
    row = df.iloc[i]
    is_wide = cpr.width_class == "WIDE"
    confirm = cfg.wide_cpr_confirm_bars if is_wide else 1
    wl = cfg.whipsaw_lookback_bars

    for side, level, bullish in (("CE", cpr.tc, True), ("PE", cpr.bc, False)):
        window = df.iloc[i - confirm + 1 : i + 1]
        if len(window) < confirm:
            continue
        if not all(_aligned(window.iloc[k], level, bullish) for k in range(len(window))):
            continue
        # Volume confirmation — only when the feed actually carries volume.
        # Dhan index-spot candles had no volume before ~2026; don't let a missing
        # field block every entry (spec: "use futures volume as proxy" — n/a here).
        vol, vol_avg = float(row["volume"]), float(row["vol_avg"])
        if vol_avg > 0 and vol > 0 and vol <= vol_avg:
            return None, f"{side}: volume {vol:.0f} <= {vol_avg:.0f} avg"
        closes = [float(x) for x in df["close"].iloc[i - wl : i + 1]]
        if _recent_whipsaw(closes, level):
            return None, f"{side}: {level:.0f} whipsawed within {wl} bars — wait for a clean break"
        kind = "breakout above TC" if bullish else "breakdown below BC"
        tag = f" ({confirm} closes, WIDE CPR)" if is_wide else ""
        return side, f"{side} buy: {kind}{tag}, EMA aligned, volume {row['volume'] / max(row['vol_avg'], 1):.1f}x"

    return None, "no CPR breakout with EMA + volume confirmation"


def entry_features(
    df: pd.DataFrame, i: int, cpr: CprContext, prev_day: pd.DataFrame
) -> dict[str, float]:
    """Signal-state snapshot at bar ``i`` — the ML feature vector for one entry."""
    row = df.iloc[i]
    c = float(row["close"])
    ts = pd.to_datetime(row["datetime"])
    p_hi, p_lo = float(prev_day["high"].max()), float(prev_day["low"].min())
    p_close = float(prev_day["close"].iloc[-1])
    ret_15m = (c / float(df["close"].iloc[max(0, i - 3)]) - 1.0) * 100.0
    return {
        "minute_of_day": float(ts.hour * 60 + ts.minute),
        "weekday": float(ts.weekday()),
        "cpr_width_pct": cpr.width_pct,
        "dist_tc_pct": (c - cpr.tc) / c * 100.0,
        "dist_bc_pct": (c - cpr.bc) / c * 100.0,
        "ema_spread_pct": (float(row["ema_fast"]) - float(row["ema_slow"])) / c * 100.0,
        "atr_pct": float(row["atr"]) / c * 100.0,
        "prev_day_range_pct": (p_hi - p_lo) / max(p_close, 1.0) * 100.0,
        "ret_15m_pct": ret_15m,
        "vol_ratio": float(row["volume"]) / max(float(row["vol_avg"]), 1.0),
    }


if __name__ == "__main__":  # ponytail self-check
    import numpy as np

    from index_ai.strategies.options_cpr.config import config_for

    cfg = config_for("NIFTY")
    prev = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01 09:15", periods=25, freq="15min"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0,
        }
    )
    cpr = cpr_context(prev, cfg)
    assert cpr.bc <= cpr.pivot <= cpr.tc
    # flat-then-breakout series: last bar closes well above TC on a volume spike
    closes = list(np.full(25, cpr.tc - 2)) + list(np.linspace(cpr.tc - 2, cpr.tc + 8, 5))
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=len(closes), freq="5min"),
            "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
            "close": closes, "volume": [1000.0] * (len(closes) - 1) + [5000.0],
        }
    )
    df = add_indicators(df, cfg)
    side, why = evaluate_entry(df, len(df) - 1, cpr, cfg)
    assert side == "CE", (side, why)
    # no volume spike -> blocked
    df2 = df.copy()
    df2.loc[df2.index[-1], "volume"] = 500.0
    df2 = add_indicators(df2, cfg)
    assert evaluate_entry(df2, len(df2) - 1, cpr, cfg)[0] is None
    print("engine.py self-check ok")
