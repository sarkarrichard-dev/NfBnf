"""
Positional (multi-day) replay of the directional futures signal.

The intraday result was always the same shape: a small real gross edge buried by
a per-trade toll. Friction is charged per *trade*, not per hour, so the arithmetic
question is simply whether the same signal, held for days instead of hours, pays
more per toll.

This is the honest way to ask that. Futures need no premium proxy — spot-replay
is a fair stand-in (small, decaying basis) — so the answer is not contaminated by
Black-Scholes guesswork the way an options test would be.

What it deliberately does NOT hide:

  * **Overnight gaps are real.** A stop is only checked against traded prices, so
    a gap through it fills at the next session's OPEN, not at the stop level.
    Intraday backtests that fill stops exactly are lying about the tail risk that
    multi-day holding actually adds.
  * **Positional futures need overnight margin** (~1.5-2L per NIFTY lot), which is
    more than a 1.4L account carries. A positive result here is a signal finding,
    not a deployable strategy at that capital.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from index_ai.charges import futures_round_trip_rupees, futures_slippage_rupees
from index_ai.strategies.futures.config import config_for
from index_ai.strategies.futures.engine import FLAT, LONG, SHORT
from index_ai.strategies.supertrend import compute_supertrend

_ = SHORT  # re-exported vocabulary


@dataclass(frozen=True)
class PositionalConfig:
    key: str
    lot_size: int
    ema_fast: int = 9
    ema_slow: int = 21
    st_period: int = 10
    st_mult: float = 3.0
    atr_period: int = 14
    stop_atr: float = 2.0          # initial stop, in daily ATRs
    trail_atr: float = 3.0         # trailing stop once armed, in daily ATRs
    arm_atr: float = 1.0           # arm the trail after this much favourable move
    max_hold_days: int = 10
    require_cpr: bool = True       # prior-day CPR must agree with the trend
    min_bars: int = 40


def positional_config(key: str, **overrides: Any) -> PositionalConfig:
    base = config_for(key)
    cfg = PositionalConfig(key=base.key, lot_size=base.lot_size)
    known = set(cfg.__dataclass_fields__)
    return PositionalConfig(**{**cfg.__dict__, **{k: v for k, v in overrides.items() if k in known}})


def daily_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Collapse intraday candles into daily OHLC."""
    df = bars.copy()
    df["_d"] = pd.to_datetime(df["datetime"]).dt.date
    agg = df.groupby("_d").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).reset_index()
    return agg.rename(columns={"_d": "date"})


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def signals(daily: pd.DataFrame, cfg: PositionalConfig) -> pd.DataFrame:
    """Per-day trend direction from the same CPR + EMA + Supertrend consensus."""
    d = daily.copy()
    d["ema_fast"] = d["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    d["ema_slow"] = d["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    d["atr"] = _atr(d, cfg.atr_period)
    st = compute_supertrend(d, period=cfg.st_period, multiplier=cfg.st_mult)
    d["st_dir"] = st["supertrend_direction"].astype(int)

    ema_b = (d["ema_fast"] > d["ema_slow"]).map({True: 1, False: -1})
    if cfg.require_cpr:
        # prior day's CPR vs today's close — same bias rule as the intraday engine
        cpr_b = []
        for i in range(len(d)):
            if i == 0:
                cpr_b.append(0)
                continue
            prev = d.iloc[i - 1]
            pivot = (prev["high"] + prev["low"] + prev["close"]) / 3.0
            bc = (prev["high"] + prev["low"]) / 2.0
            tc = 2.0 * pivot - bc
            lo, hi = min(bc, tc), max(bc, tc)
            c = d["close"].iloc[i]
            cpr_b.append(1 if c > hi else -1 if c < lo else 0)
        d["cpr_bias"] = cpr_b
    else:
        d["cpr_bias"] = 0

    direction = pd.Series(FLAT, index=d.index)
    direction[(d["cpr_bias"] >= 0) & (ema_b == 1) & (d["st_dir"] == 1)] = LONG
    direction[(d["cpr_bias"] <= 0) & (ema_b == -1) & (d["st_dir"] == -1)] = SHORT
    d["direction"] = direction
    return d


def replay(daily: pd.DataFrame, cfg: PositionalConfig) -> list[dict[str, Any]]:
    """Hold across sessions. Entries and flip-exits fill at the NEXT day's open."""
    d = signals(daily, cfg)
    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None

    for i in range(cfg.min_bars, len(d) - 1):
        row, nxt = d.iloc[i], d.iloc[i + 1]
        direction = int(row["direction"])
        atr = float(row["atr"]) or 1.0

        if pos is not None:
            dd = pos["dir"]
            hi, lo = float(row["high"]), float(row["low"])
            pos["peak"] = max(pos["peak"], hi) if dd == LONG else min(pos["peak"], lo)
            fav = (pos["peak"] - pos["entry"]) * dd
            if not pos["armed"] and fav >= cfg.arm_atr * pos["entry_atr"]:
                pos["armed"] = True
            if pos["armed"]:
                trail = pos["peak"] - dd * cfg.trail_atr * atr
                pos["stop"] = max(pos["stop"], trail) if dd == LONG else min(pos["stop"], trail)

            # gap risk is the point of this test: a stop only fills at a traded
            # price, so a gap through it fills at the open, worse than the stop
            gapped = (float(row["open"]) <= pos["stop"]) if dd == LONG else (float(row["open"]) >= pos["stop"])
            hit = (lo <= pos["stop"]) if dd == LONG else (hi >= pos["stop"])
            if gapped:
                _close(trades, pos, float(row["open"]), row["date"], "gap_through_stop", cfg)
                pos = None
                continue
            if hit:
                _close(trades, pos, pos["stop"], row["date"], "stop", cfg)
                pos = None
                continue
            pos["days"] += 1
            if direction != FLAT and direction != dd:
                _close(trades, pos, float(nxt["open"]), nxt["date"], "trend_flip", cfg)
                pos = None
            elif pos["days"] >= cfg.max_hold_days:
                _close(trades, pos, float(nxt["open"]), nxt["date"], "max_hold", cfg)
                pos = None
            continue

        if direction == FLAT:
            continue
        entry = float(nxt["open"])
        pos = {
            "dir": direction, "entry": entry, "entry_date": str(nxt["date"]),
            "entry_atr": atr, "peak": entry,
            "stop": entry - direction * cfg.stop_atr * atr, "armed": False, "days": 0,
        }

    if pos is not None:
        last = d.iloc[-1]
        _close(trades, pos, float(last["close"]), last["date"], "series_end", cfg)
    return trades


def _close(trades: list[dict[str, Any]], pos: dict[str, Any], px: float,
           date: Any, reason: str, cfg: PositionalConfig) -> None:
    pts = (px - pos["entry"]) * pos["dir"]
    gross = pts * cfg.lot_size
    cost = (futures_round_trip_rupees(pos["entry"], cfg.lot_size, cfg.key)
            + futures_slippage_rupees(cfg.lot_size, cfg.key))
    trades.append({
        "instrument": cfg.key,
        "direction": "LONG" if pos["dir"] == LONG else "SHORT",
        "entry_date": pos["entry_date"], "exit_date": str(date),
        "session": pos["entry_date"],
        "hold_days": pos["days"], "reason": reason,
        "entry": round(pos["entry"], 2), "exit": round(px, 2),
        "points": round(pts, 2),
        "gross_rupees": round(gross, 2), "friction_rupees": round(cost, 2),
        "net_rupees": round(gross - cost, 2),
    })


def run(instrument_key: str, bars: pd.DataFrame, *, sessions: int = 0,
        **overrides: Any) -> list[dict[str, Any]]:
    cfg = positional_config(instrument_key, **overrides)
    daily = daily_frame(bars)
    if sessions:
        daily = daily.tail(sessions).reset_index(drop=True)
    if len(daily) <= cfg.min_bars + 2:
        return []
    return replay(daily, cfg)


if __name__ == "__main__":  # ponytail self-check
    import numpy as np

    n = 200
    close = np.linspace(24000, 26000, n)          # clean uptrend
    bars = pd.DataFrame({
        "datetime": pd.date_range("2025-01-01 09:15", periods=n, freq="D"),
        "open": close, "high": close + 60, "low": close - 60, "close": close, "volume": 0.0,
    })
    cfg = positional_config("NIFTY")
    assert cfg.lot_size == 65
    daily = daily_frame(bars)
    assert len(daily) == n and {"open", "high", "low", "close"} <= set(daily.columns)
    sig = signals(daily, cfg)
    assert int(sig["direction"].iloc[-1]) == LONG, "sustained uptrend must read LONG"
    trades = run("NIFTY", bars)
    assert trades, "an uptrend should produce at least one positional long"
    assert all(t["hold_days"] >= 0 for t in trades)
    assert all(t["friction_rupees"] > 0 for t in trades)
    t0 = trades[0]
    assert t0["direction"] == "LONG" and t0["net_rupees"] == round(
        t0["gross_rupees"] - t0["friction_rupees"], 2)
    print(f"positional.py self-check ok — {len(trades)} trades, "
          f"avg hold {sum(t['hold_days'] for t in trades)/len(trades):.1f}d")
