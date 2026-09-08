"""FVG Scalp — 5-minute Fair Value Gap entries with a candlestick trigger and a
stack of "advanced TA" filters, exiting on the shared P&L trailing engine.

The core setup is one idea: price ran fast enough to leave a 3-candle imbalance
(a Fair Value Gap), then came back to retest it. A gap is a demand zone
(bullish) or supply zone (bearish); a confirming candlestick pattern at the
retest is the trigger.

Two contexts qualify a setup — chosen per setup, reported as ``mode``:

* **continuation** — the 15m Supertrend and the 5m swing structure both agree
  with the gap direction. Trade the gap as a with-trend pullback entry.
* **reversal** — the trend is flat/mild and price is stretched
  (``stretch_atr`` × ATR from the fast EMA) into an *opposing* gap. Fade back
  toward the mean / the unfilled gap.

Filters (all on by construction, tunable): a minimum gap width, an impulse
candle that is genuinely large *and* high-volume, a 15m trend read, 5m market
structure, and an IST session window (skip the dead Asian afternoon).

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame; the 15m
frame is derived inside. Pure: returns ``(state, event)``. The lane owns sizing,
journalling and Telegram, same as the other simple strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from crypto.strategies.fairvalue import find_fvgs
from crypto.strategies.indicators import atr_last, ema, pivot_high, pivot_low, supertrend_dir
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class FvgScalpConfig:
    atr_len: int = 14
    fvg_min_atr: float = 0.25          # gap width ≥ this × ATR — a real imbalance
    impulse_atr_mult: float = 1.2      # the gap's impulse candle range ≥ this × ATR
    impulse_vol_mult: float = 1.3      # …and its volume ≥ this × mean(volume)
    vol_lookback: int = 20
    fvg_max_age_bars: int = 24         # forget an untouched gap after ~2h
    st_period: int = 10               # 15m Supertrend
    st_mult: float = 3.0
    struct_left: int = 5              # 5m swing pivots for market structure
    struct_right: int = 2
    ema_fast: int = 21
    stretch_atr: float = 1.5          # reversal: price this far from ema_fast
    session_start_ist: int = 13       # London pre-open, IST hour
    session_end_ist: int = 23         # NY afternoon, IST hour
    exit_on_session_end: bool = True
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _resample_15m(c5: pd.DataFrame) -> pd.DataFrame:
    """5m OHLCV → 15m, numpy segment-reduce (pandas ``.resample`` is far slower
    and this runs once per replayed bar)."""
    idx = pd.DatetimeIndex(pd.to_datetime(c5["datetime"]))
    o, h, low, c, v = (c5[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    b = idx.floor("15min").to_numpy()
    starts = np.concatenate(([0], np.flatnonzero(b[1:] != b[:-1]) + 1))
    ends = np.concatenate((starts[1:], [len(b)])) - 1
    return pd.DataFrame(
        {
            "datetime": idx.to_numpy()[starts],
            "open": o[starts],
            "high": np.maximum.reduceat(h, starts),
            "low": np.minimum.reduceat(low, starts),
            "close": c[ends],
            "volume": np.add.reduceat(v, starts),
        }
    )


def _trend_15m(c5: pd.DataFrame, cfg: FvgScalpConfig, st: dict[str, Any]) -> int:
    """15m Supertrend direction (+1/-1/0), cached per 15-minute bucket — it only
    changes when a new 15m bar closes."""
    bucket = f"{pd.Timestamp(c5['datetime'].iloc[-1]).floor('15min')}|{cfg.st_period}|{cfg.st_mult}"
    if st.get("_tb") == bucket:
        return int(st.get("_td") or 0)
    d = supertrend_dir(_resample_15m(c5), cfg.st_period, cfg.st_mult)
    st["_tb"], st["_td"] = bucket, d
    return d


def _pattern(c5: pd.DataFrame) -> int:
    """+1 bullish / -1 bearish / 0 from the last closed 5m bar
    (engulfing / hammer / shooting-star)."""
    if len(c5) < 2:
        return 0
    o, h, low, c = (float(c5[k].iloc[-1]) for k in ("open", "high", "low", "close"))
    po, pc = float(c5["open"].iloc[-2]), float(c5["close"].iloc[-2])
    body = abs(c - o)
    rng = (h - low) or 1e-9
    upper, lower = h - max(o, c), min(o, c) - low
    if c >= o and pc < po and c > po and o <= pc:      # bullish engulfing
        return 1
    if c < o and pc > po and c < po and o >= pc:       # bearish engulfing
        return -1
    if lower >= 2 * body and upper <= body and body / rng < 0.4:   # hammer
        return 1
    if upper >= 2 * body and lower <= body and body / rng < 0.4:   # shooting star
        return -1
    return 0


def _structure(close: pd.Series, cfg: FvgScalpConfig) -> int:
    """+1 higher-high & higher-low, -1 lower-low & lower-high, else 0 — from the
    last two confirmed 5m swings on each side."""
    ph = pivot_high(close, cfg.struct_left, cfg.struct_right).dropna()
    pl = pivot_low(close, cfg.struct_left, cfg.struct_right).dropna()
    if len(ph) < 2 or len(pl) < 2:
        return 0
    hh = float(ph.iloc[-1]) > float(ph.iloc[-2])
    hl = float(pl.iloc[-1]) > float(pl.iloc[-2])
    lh = float(ph.iloc[-1]) < float(ph.iloc[-2])
    ll = float(pl.iloc[-1]) < float(pl.iloc[-2])
    if hh and hl:
        return 1
    if lh and ll:
        return -1
    return 0


def _impulse_ok(c5: pd.DataFrame, idx: int, atr_val: float, cfg: FvgScalpConfig) -> bool:
    """Was the candle *before* the gap-completing bar a real impulse — large
    range and above-average volume?"""
    j = idx - 1
    if j < cfg.vol_lookback:
        return False
    rng = float(c5["high"].iloc[j] - c5["low"].iloc[j])
    vol = float(c5["volume"].iloc[j])
    mean_vol = float(c5["volume"].iloc[j - cfg.vol_lookback : j].mean() or 0.0)
    return rng >= cfg.impulse_atr_mult * atr_val and (mean_vol <= 0 or vol >= cfg.impulse_vol_mult * mean_vol)


def _in_session(ts: pd.Timestamp, cfg: FvgScalpConfig) -> bool:
    hour = pd.Timestamp(ts).tz_convert("Asia/Kolkata").hour if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).hour
    if cfg.session_start_ist <= cfg.session_end_ist:
        return cfg.session_start_ist <= hour < cfg.session_end_ist
    return hour >= cfg.session_start_ist or hour < cfg.session_end_ist  # window wraps midnight


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: FvgScalpConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "fvg_scalp", "asset": symbol, "event": "none"}

    need = max(cfg.atr_len, cfg.vol_lookback, cfg.struct_left + cfg.struct_right, cfg.ema_fast) + 6
    if len(candles) < need:
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    ts = c5["datetime"].iloc[-1]
    atr_val = atr_last(c5, cfg.atr_len)
    pos = st["position"]

    # ---- manage an open position ----
    if pos:
        side = pos["side"]
        reason = None
        if cfg.exit_on_session_end and not _in_session(ts, cfg):
            reason = "session end"
        elif trail_reason := update_and_check(pos, price, cfg.trail):
            reason = trail_reason
        elif side == "long" and float(close.iloc[-1]) < pos["fvg_lo"]:
            reason = "fvg invalidated"
        elif side == "short" and float(close.iloc[-1]) > pos["fvg_hi"]:
            reason = "fvg invalidated"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=str(ts))
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    # ---- look for an entry ----
    if not _in_session(ts, cfg):
        ev.update(event="wait", reason="outside session window")
        return st, ev
    if atr_val <= 0:
        ev.update(event="wait", reason="no ATR yet")
        return st, ev

    gaps = find_fvgs(c5, atr_val=atr_val, min_atr=cfg.fvg_min_atr, max_age_bars=cfg.fvg_max_age_bars)
    if not gaps:
        ev.update(event="wait", reason="no live FVG")
        return st, ev

    pat = _pattern(c5)
    if pat == 0:
        ev.update(event="wait", reason="no candlestick trigger")
        return st, ev

    trend = _trend_15m(c5, cfg, st)
    struct = _structure(close, cfg)
    ema_fast = float(ema(close, cfg.ema_fast).iloc[-1])
    bar_low = float(c5["low"].iloc[-1])
    bar_high = float(c5["high"].iloc[-1])

    # most recent gap whose zone this bar retested, in the pattern's direction
    for g in reversed(gaps):
        if g["dir"] != pat:
            continue
        tapped = bar_low <= g["hi"] and bar_high >= g["lo"]
        if not tapped:
            continue
        if not _impulse_ok(c5, g["idx"], atr_val, cfg):
            continue
        want = "long" if pat == 1 else "short"
        stretch = (price - ema_fast) / atr_val if pat == -1 else (ema_fast - price) / atr_val
        continuation = trend == pat and struct in (pat, 0)
        reversal = trend != -pat and stretch >= cfg.stretch_atr
        if not (continuation or reversal):
            continue
        mode = "continuation" if continuation else "reversal"
        st["position"] = {
            "side": want,
            "entry_price": price,
            "entry_time": str(ts),
            "fvg_lo": g["lo"],
            "fvg_hi": g["hi"],
            "mode": mode,
        }
        ev.update(
            event="enter",
            side=want,
            price=price,
            reason=f"{mode} off {'bull' if pat == 1 else 'bear'} FVG {g['lo']:,.2f}-{g['hi']:,.2f}",
            mode=mode,
            ts=str(ts),
        )
        return st, ev

    ev.update(event="wait", reason="FVG retest without a qualifying context")
    return st, ev


if __name__ == "__main__":  # self-check — bullish FVG, retrace, hammer → enter long → trail exit
    n = 80
    c = [100.0 + 0.1 * i for i in range(40)]      # 0..39  slow uptrend
    c += [104.0, 105.0]                           # 40,41
    c += [111.0]                                  # 42     impulse candle (huge green)
    c += [111.5, 112.0]                           # 43,44  → FVG: low[43] >> high[41]
    c += [111.0 - 0.55 * i for i in range(1, 11)] # 45..54 retrace down into the gap
    c += [105.6] * (n - len(c))                   # 55..   drift at the gap floor
    o = [x - 0.1 for x in c]
    hi = [x + 0.5 for x in c]
    lo = [x - 0.5 for x in c]
    vol = [10.0] * n
    hi[42], lo[42], vol[42] = 111.8, 104.9, 90.0  # impulse: wide range + volume spike
    lo[43] = 110.6                                 # gap floor well above high[41]≈105.6
    # entry bar 55: a hammer inside the gap zone
    o[55], c[55], hi[55], lo[55] = 106.0, 106.4, 106.7, 104.4
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-08 14:00", periods=n, freq="5min", tz="Asia/Kolkata"),
            "open": o, "high": hi, "low": lo, "close": c, "volume": vol,
        }
    )
    cfg = FvgScalpConfig(atr_len=10, vol_lookback=10, struct_left=3, struct_right=2,
                         ema_fast=10, stretch_atr=0.3, fvg_min_atr=0.1)
    state, fired = None, None
    for i in range(30, n):
        state, evt = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if evt["event"] == "enter":
            fired = evt
            break
    assert fired and fired["side"] == "long", fired
    assert state["position"]["mode"] in ("continuation", "reversal"), state["position"]
    print("crypto.strategies.fvg_scalp self-check ok —", fired["reason"])
