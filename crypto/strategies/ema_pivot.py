"""EMA + Pivot — 5-minute trend-continuation entries where a standard daily
pivot break lines up with a stacked, sloping 9/13/21 EMA fan.

From the CoinSwitch "EMA + Pivot" video (Ahmed Lekhan). The idea: combine two
kinds of support/resistance — **horizontal** (standard daily pivots) and
**dynamic** (the EMA fan) — and only trade when both agree.

- **Trend:** the three EMAs (9, 13, 21 close) must be stacked *and* sloping in
  order — 9 > 13 > 21 rising for longs, mirrored for shorts.
- **Location:** price on the supporting side of the daily pivot P (above for
  longs, below for shorts).
- **Trigger:** the last 5m close breaks a standard pivot level (P / R1-R3 /
  S1-S3) it was sitting below/above — a fresh horizontal break in the trend
  direction. Skip if the trigger candle is oversized (retracement risk).
- **Confluence:** the video's "high-probability" rule — the broken pivot must
  sit close to the EMA fan (``confluence_atr``), and each level trades once per
  UTC day (``one_per_level``). The dataclass ships these off so the self-check
  exercises the base mechanism; ``crypto.lanes`` turns confluence on
  operationally (a 90-day sweep showed it roughly halves the bleed — but the
  strategy is still net-negative after costs and stays disabled).
- **Exit:** the shared P&L trailing engine, a close back through the 9 EMA, or
  price stretched far from the 9 EMA (mean-reversion take-profit).

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame (needs
~3 days so the previous UTC day's pivots can be built). Pure: returns
``(state, event)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import atr_last, ema
from crypto.strategies.pivots import crossed_down, crossed_up, standard_pivots
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class EmaPivotConfig:
    ema_fast: int = 9
    ema_mid: int = 13
    ema_slow: int = 21
    slope_lookback: int = 3          # bars: each EMA must have moved in-trend over this span
    atr_len: int = 14
    big_candle_atr: float = 2.0      # skip the entry if the trigger candle's range > this × ATR
    stretch_atr: float = 3.0         # price this far from the 9 EMA → take profit (mean revert)
    # the video's "high-probability" filter: the broken pivot and the EMA fan
    # must sit at the same place. 0 = off (any pivot break with a stacked fan).
    confluence_atr: float = 0.0      # broken pivot within this × ATR of the slow EMA
    min_fan_atr: float = 0.0         # 9↔21 EMA spread must exceed this × ATR (skip a flat fan)
    one_per_level: bool = True       # one entry per pivot level per UTC day
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None, "traded_day": None, "traded_levels": []}


def _fan(close: pd.Series, cfg: EmaPivotConfig) -> tuple[float, float, float, float, float, float]:
    f = ema(close, cfg.ema_fast)
    m = ema(close, cfg.ema_mid)
    s = ema(close, cfg.ema_slow)
    lb = cfg.slope_lookback
    return (
        float(f.iloc[-1]), float(m.iloc[-1]), float(s.iloc[-1]),
        float(f.iloc[-1 - lb]), float(m.iloc[-1 - lb]), float(s.iloc[-1 - lb]),
    )


def _trend(close: pd.Series, cfg: EmaPivotConfig) -> int:
    """+1 stacked & rising (9>13>21, all sloping up), -1 mirrored, 0 otherwise."""
    if len(close) < cfg.ema_slow + cfg.slope_lookback + 2:
        return 0
    f, m, s, f0, m0, s0 = _fan(close, cfg)
    if f > m > s and f > f0 and m > m0 and s > s0:
        return 1
    if f < m < s and f < f0 and m < m0 and s < s0:
        return -1
    return 0


def _pivot_break(c5: pd.DataFrame, piv: dict[str, float], direction: int) -> tuple[str, float] | None:
    """The pivot level the last closed bar just broke in ``direction``, if any."""
    if len(c5) < 2 or not piv:
        return None
    prev_c = float(c5["close"].iloc[-2])
    last_c = float(c5["close"].iloc[-1])
    test = crossed_up if direction == 1 else crossed_down
    hits = [(k, v) for k, v in piv.items() if test(prev_c, last_c, v)]
    if not hits:
        return None
    # the level nearest the break
    return min(hits, key=lambda kv: abs(kv[1] - last_c))


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: EmaPivotConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ema_pivot", "asset": symbol, "event": "none"}

    need = max(cfg.ema_slow + cfg.slope_lookback + 2, cfg.atr_len + 2)
    if len(candles) < need:
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    utc_day = str(pd.Timestamp(c5["datetime"].iloc[-1]).tz_convert("UTC").date())
    atr_val = atr_last(c5, cfg.atr_len)
    ema_fast_now = float(ema(close, cfg.ema_fast).iloc[-1])
    ema_slow_now = float(ema(close, cfg.ema_slow).iloc[-1])
    pos = st["position"]

    if st.get("traded_day") != utc_day:  # per-UTC-day level tracking
        st["traded_day"], st["traded_levels"] = utc_day, []

    # ---- manage an open position ----
    if pos:
        side = pos["side"]
        reason = None
        if trail_reason := update_and_check(pos, price, cfg.trail):
            reason = trail_reason
        elif side == "long" and price < ema_fast_now:
            reason = "closed through the 9 EMA"
        elif side == "short" and price > ema_fast_now:
            reason = "closed through the 9 EMA"
        elif atr_val > 0 and abs(price - ema_fast_now) / atr_val >= cfg.stretch_atr:
            reason = f"stretched {abs(price - ema_fast_now) / atr_val:.1f}×ATR from the 9 EMA"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    # ---- look for an entry ----
    if atr_val <= 0:
        ev.update(event="wait", reason="no ATR yet")
        return st, ev

    trend = _trend(close, cfg)
    if trend == 0:
        ev.update(event="wait", reason="EMA fan not stacked/sloping")
        return st, ev

    piv = standard_pivots(c5)
    if not piv:
        ev.update(event="wait", reason="no prior-day pivots yet")
        return st, ev

    # price must be on the supporting side of the day's pivot
    if trend == 1 and price <= piv["P"]:
        ev.update(event="wait", reason="uptrend but price is below the daily pivot")
        return st, ev
    if trend == -1 and price >= piv["P"]:
        ev.update(event="wait", reason="downtrend but price is above the daily pivot")
        return st, ev

    # EMA fan must be genuinely separated, not a flat cluster (chop)
    if cfg.min_fan_atr > 0 and abs(ema_fast_now - ema_slow_now) < cfg.min_fan_atr * atr_val:
        ev.update(event="wait", reason="EMA fan too tight — chop")
        return st, ev

    brk = _pivot_break(c5, piv, trend)
    if not brk:
        ev.update(event="wait", reason="no pivot break in the trend direction")
        return st, ev

    lvl_name, lvl = brk

    if cfg.one_per_level and lvl_name in st["traded_levels"]:
        ev.update(event="wait", reason=f"{lvl_name} already traded today")
        return st, ev

    # the video's "high-probability" filter: pivot and EMA at the same place
    if cfg.confluence_atr > 0 and abs(lvl - ema_slow_now) > cfg.confluence_atr * atr_val:
        ev.update(event="wait", reason="pivot break too far from the EMA fan — low-probability")
        return st, ev

    rng = float(c5["high"].iloc[-1] - c5["low"].iloc[-1])
    if rng > cfg.big_candle_atr * atr_val:
        ev.update(event="wait", reason="trigger candle oversized — waiting for a retrace")
        return st, ev

    want = "long" if trend == 1 else "short"
    st["traded_levels"] = [*st["traded_levels"], lvl_name]
    st["position"] = {"side": want, "entry_price": price, "entry_time": ts, "pivot": lvl_name}
    ev.update(
        event="enter",
        side=want,
        price=price,
        reason=f"{lvl_name} {lvl:,.2f} break with the 9/13/21 fan stacked {'up' if trend == 1 else 'down'}",
        ts=ts,
    )
    return st, ev


if __name__ == "__main__":  # self-check — build an uptrend that breaks a pivot with a stacked fan
    import numpy as np

    n = 700
    idx = pd.date_range("2026-09-06 00:00", periods=n, freq="5min", tz="UTC")
    # day 1: range 96–104 (sets the pivots); day 2: a clean rally through them
    day1 = 100.0 + np.sin(np.linspace(0, 8, 288)) * 4
    day2 = np.linspace(100.0, 118.0, n - 288)
    px = np.concatenate([day1, day2])
    df = pd.DataFrame(
        {"datetime": idx, "open": px, "high": px + 0.4, "low": px - 0.4, "close": px,
         "volume": [10.0] * n}
    )
    cfg = EmaPivotConfig(slope_lookback=2)
    state, fired = None, None
    for i in range(300, n):
        state, evt = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if evt["event"] == "enter":
            fired = evt
            break
    assert fired and fired["side"] == "long", fired
    assert state["position"]["pivot"] in ("P", "R1", "R2", "R3"), state["position"]
    print("crypto.strategies.ema_pivot self-check ok —", fired["reason"])
