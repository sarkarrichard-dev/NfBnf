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

from index_ai.strategies.candlestick_sr import intraday_candle_trend
from index_ai.strategies.options_cpr.config import OptionsCprConfig
from index_ai.strategies.strategy import previous_day_cpr
from index_ai.strategies.supertrend import supertrend_snapshot


@dataclass(frozen=True)
class CprContext:
    pivot: float
    bc: float
    tc: float
    width_pct: float
    width_class: str  # "NARROW" | "NORMAL" | "WIDE"


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


def trend15_read(
    prev15: pd.DataFrame, today15: pd.DataFrame, cfg: OptionsCprConfig, *, at_ts=None
) -> dict[str, float]:
    """15-minute trend + swing S&R for the directional-sell lane.

    ``direction`` (entry gate) is +1 / -1 only when the 15m EMA (fast/slow),
    Supertrend (``cfg.st_period`` / ``cfg.st_multiplier``) and candle structure
    all agree on the bar closed by ``at_ts`` (or the last closed bar when
    ``at_ts`` is None); otherwise 0. ``ema_dir`` is the looser EMA-only read used
    for the trend-flip *exit* — as strict as the old ``_align15``.
    ``swing_high`` / ``swing_low`` bound the last ``cfg.trend15_swing_lookback``
    bars of *today's* session (the levels a live move just broke).
    """
    full = pd.concat([prev15, today15], ignore_index=True)
    today_only = today15
    if at_ts is not None:
        ts = pd.Timestamp(at_ts)
        full = full[
            (pd.to_datetime(full["datetime"]) + pd.Timedelta("15min") <= ts).to_numpy()
        ].reset_index(drop=True)
        today_only = today15[
            (pd.to_datetime(today15["datetime"]) + pd.Timedelta("15min") <= ts).to_numpy()
        ].reset_index(drop=True)
    if len(full) < max(cfg.ema_slow, cfg.st_period) + 2:
        return {"direction": 0.0, "ema_dir": 0.0, "swing_high": 0.0, "swing_low": 0.0}
    ef = float(full["close"].ewm(span=cfg.ema_fast, adjust=False).mean().iloc[-1])
    es = float(full["close"].ewm(span=cfg.ema_slow, adjust=False).mean().iloc[-1])
    ema_dir = 1 if ef > es else -1 if ef < es else 0
    st_dir = int(
        supertrend_snapshot(full, period=cfg.st_period, multiplier=cfg.st_multiplier).get(
            "direction"
        )
        or 0
    )
    struct = intraday_candle_trend(full, lookback=15)
    direction = ema_dir if ema_dir != 0 and ema_dir == st_dir else 0
    if direction == 1 and struct == "DOWN":
        direction = 0
    elif direction == -1 and struct == "UP":
        direction = 0
    swing_src = today_only if len(today_only) >= 2 else full
    tail = swing_src.tail(max(2, cfg.trend15_swing_lookback))
    return {
        "direction": float(direction),
        "ema_dir": float(ema_dir),
        "swing_high": float(tail["high"].astype(float).max()),
        "swing_low": float(tail["low"].astype(float).min()),
    }


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
        return (
            side,
            f"{side} buy: {kind}{tag}, EMA aligned, volume {row['volume'] / max(row['vol_avg'], 1):.1f}x",
        )

    return None, "no CPR breakout with EMA + volume confirmation"


def add_pema(df: pd.DataFrame, cfg: OptionsCprConfig) -> pd.DataFrame:
    """Three EMAs of typical price (PivotBoss PEMA ribbon) for the pullback-
    rejection entry mode. Default lengths (21/34/55) and construction match
    the public "PivotBoss PEMA Method" script studied live off Richard's own
    TradingView chart (author Nanda86) — not reverse-engineered."""
    out = df.copy()
    hlc3 = (out["high"] + out["low"] + out["close"]) / 3.0
    out["pema_fast"] = hlc3.ewm(span=cfg.pema_fast, adjust=False).mean()
    out["pema_mid"] = hlc3.ewm(span=cfg.pema_mid, adjust=False).mean()
    out["pema_slow"] = hlc3.ewm(span=cfg.pema_slow, adjust=False).mean()
    return out


def evaluate_entry_pema_pullback(
    df: pd.DataFrame, i: int, cpr: CprContext, cfg: OptionsCprConfig
) -> tuple[str | None, str]:
    """PivotBoss PEMA method: stacked + sloping ribbon = trend; entry only on a
    pullback to the fast PEMA line followed by a same-bar rejection close back
    in the trend direction (the author's own description of the method, not a
    breakout). ``df`` must already carry ``pema_fast/mid/slow`` (``add_pema``).
    Opposite entry style from ``evaluate_entry``: patience for a pullback
    instead of chasing the initial break.
    """
    lb = cfg.pema_pullback_lookback
    slope_lb = cfg.pema_slope_lookback
    warmup = max(cfg.warmup_bars, lb + 1, slope_lb + 1)
    if i < warmup:
        return None, "warming up"
    row = df.iloc[i]
    c, o = float(row["close"]), float(row["open"])
    pf, pm, ps = float(row["pema_fast"]), float(row["pema_mid"]), float(row["pema_slow"])
    # Slope over several bars, not one tick — a 1-bar EMA wiggle is noise on a 5m
    # chart, not the sustained slope the PEMA method actually means by "trending".
    pf_then = float(df.iloc[i - slope_lb]["pema_fast"])
    stack_pct = abs(pf - ps) / c * 100.0
    window = df.iloc[i - lb : i]  # bars *before* the current one

    bull_trend = pf > pm > ps and pf > pf_then and stack_pct >= cfg.pema_min_stack_pct
    bear_trend = pf < pm < ps and pf < pf_then and stack_pct >= cfg.pema_min_stack_pct
    if bull_trend:
        ran_up = bool((window["close"] > pm).any())  # a real leg up to pull back from
        pulled_back = bool((window["low"] <= window["pema_fast"]).any())
        if ran_up and pulled_back and c > pf and c > o:
            return (
                "CE",
                f"PEMA pullback: ribbon {stack_pct:.2f}% stacked, {lb}-bar dip to fast PEMA "
                f"({pf:.1f}), bullish rejection close",
            )
        return None, "PEMA uptrend, no clean pullback+rejection yet"
    if bear_trend:
        ran_down = bool((window["close"] < pm).any())
        pulled_back = bool((window["high"] >= window["pema_fast"]).any())
        if ran_down and pulled_back and c < pf and c < o:
            return (
                "PE",
                f"PEMA pullback: ribbon {stack_pct:.2f}% stacked, {lb}-bar rally to fast PEMA "
                f"({pf:.1f}), bearish rejection close",
            )
        return None, "PEMA downtrend, no clean pullback+rejection yet"
    return None, "PEMA not stacked/sloping — no trend"


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
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000.0,
        }
    )
    cpr = cpr_context(prev, cfg)
    assert cpr.bc <= cpr.pivot <= cpr.tc
    # flat-then-breakout series: last bar closes well above TC on a volume spike
    closes = list(np.full(25, cpr.tc - 2)) + list(np.linspace(cpr.tc - 2, cpr.tc + 8, 5))
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=len(closes), freq="5min"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000.0] * (len(closes) - 1) + [5000.0],
        }
    )
    df = add_indicators(df, cfg)
    side, why = evaluate_entry(df, len(df) - 1, cpr, cfg)
    assert side == "CE", (side, why)
    # trend15_read: a clean 15m uptrend reads +1 with a sane swing band
    up15 = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=40, freq="15min"),
            "open": np.linspace(100, 140, 40),
            "high": np.linspace(101, 141, 40),
            "low": np.linspace(99, 139, 40),
            "close": np.linspace(100, 140, 40),
            "volume": 1000.0,
        }
    )
    t15 = trend15_read(up15.head(0).reindex(columns=up15.columns), up15, cfg)
    assert t15["direction"] == 1.0 and t15["swing_low"] < t15["swing_high"], t15
    # no volume spike -> blocked
    df2 = df.copy()
    df2.loc[df2.index[-1], "volume"] = 500.0
    df2 = add_indicators(df2, cfg)
    assert evaluate_entry(df2, len(df2) - 1, cpr, cfg)[0] is None

    # PEMA pullback-rejection: uptrend ribbon, a dip that touches the fast PEMA,
    # then a bullish rejection close -> CE. No pullback yet -> None.
    up = list(np.linspace(100, 160, 60))
    pdf = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-03 09:15", periods=62, freq="5min"),
            "open": up + [155.0, 158.0],
            "high": [x + 1 for x in up] + [156.0, 159.0],
            "low": [x - 1 for x in up] + [148.0, 157.0],
            "close": up + [150.0, 158.5],
            "volume": 1000.0,
        }
    )
    pdf = add_pema(pdf, cfg)
    side, why = evaluate_entry_pema_pullback(pdf, len(pdf) - 1, cpr, cfg)
    assert side == "CE", (side, why)
    assert evaluate_entry_pema_pullback(pdf, 30, cpr, cfg)[0] is None  # mid-trend, no pullback yet
    print("engine.py self-check ok")
