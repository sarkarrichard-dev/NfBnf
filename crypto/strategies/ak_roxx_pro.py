"""AK Roxx Pro — 5-minute confluence signal: a stacked/sloping 21/34/55 EMA
ribbon, price fully outside the previous hour's CPR, and the signal candle
neither oversized nor pressed against an opposing swing level.

Reconstructed from ``crypto/strategies/ak_roxx_pro.md`` (the locked TradingView
indicator "AK Algo Buy and Sell Signals"). This is the clean-room Python port,
kept in the same shape as ``ema_pivot`` — the two share most of their gates.

- **Trend:** EMA(21) > EMA(34) > EMA(55) and all three sloping up over
  ``slope_lookback`` bars → long bias; mirrored → short. Else no trade.
- **1H CPR gate:** from the previous completed 1-hour bar, ``P=(H+L+C)/3``,
  ``BC=(H+L)/2``, ``TC=2P-BC``. A long needs ``close > TC``; a short needs
  ``close < BC``. Inside the range is the indicator's "NO TRADE ZONE".
- **Swing-S/R gate:** confirmed ``pivot_high`` / ``pivot_low`` levels. Skip a
  long that is within ``near_pct`` of a resistance above; mirrored for shorts.
  (The indicator tags each level with its net volume — cosmetic for a gate, so
  the sign is dropped here.)
- **Big-candle gate:** signal-bar range > ``big_candle_atr`` × ATR → skip.
- **Exit:** the shared P&L trailing engine, or a fixed 1:2 target measured off
  the initial stop distance. No opposite signal in between — ride the trail.

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame (needs a
few hundred bars so the 55 EMA settles and the prior hour exists). Pure.

Backtested on Delta 5m (60d, BTC/ETH/SOL) 2026-09-09: net −$8k / 3.5k trades,
gross flat (no edge). It did not clear — see ``ak_roxx_pro.md`` "Backtest
result". Kept as documented-dead: wired to ``crypto/backtest.py`` only, never to
``crypto/lanes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import atr_last, ema, pivot_high, pivot_low
from crypto.strategies.trailing import TrailConfig, pnl_pct, update_and_check


@dataclass(frozen=True)
class AkRoxxConfig:
    ma_fast: int = 21
    ma_mid: int = 34
    ma_slow: int = 55
    slope_lookback: int = 3          # each EMA must have moved in-trend over this span
    atr_len: int = 14
    big_candle_atr: float = 2.0      # skip the entry if the signal candle's range > this × ATR
    pivot_left: int = 12             # swing-S/R confirmation window (guess ~10-20)
    pivot_right: int = 3
    sr_keep: int = 4                 # how many recent swings each side to keep as levels
    near_pct: float = 0.20           # skip if within this % of the opposing swing level (guess 0.15-0.3)
    require_beyond_cpr: bool = True   # price must sit fully outside the previous 1H CPR
    rr: float = 2.0                  # fixed target = rr × initial stop distance (1:2)
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _trend(close: pd.Series, cfg: AkRoxxConfig) -> int:
    """+1 ribbon stacked & rising (21>34>55, all sloping up), -1 mirrored, 0 else."""
    if len(close) < cfg.ma_slow + cfg.slope_lookback + 2:
        return 0
    lb = cfg.slope_lookback
    f, m, s = ema(close, cfg.ma_fast), ema(close, cfg.ma_mid), ema(close, cfg.ma_slow)
    fn, mn, sn = float(f.iloc[-1]), float(m.iloc[-1]), float(s.iloc[-1])
    f0, m0, s0 = float(f.iloc[-1 - lb]), float(m.iloc[-1 - lb]), float(s.iloc[-1 - lb])
    if fn > mn > sn and fn > f0 and mn > m0 and sn > s0:
        return 1
    if fn < mn < sn and fn < f0 and mn < m0 and sn < s0:
        return -1
    return 0


def _prev_hour_cpr(c5: pd.DataFrame) -> tuple[float, float, float] | None:
    """(P, BC, TC) from the previous completed 1-hour bar, or None if it isn't
    fully inside the window."""
    dt = c5["datetime"]
    cur_hour = pd.Timestamp(dt.iloc[-1]).floor("1h")
    prev_hour = cur_hour - pd.Timedelta(hours=1)
    m = (dt >= prev_hour) & (dt < cur_hour)
    if not m.any() or (dt.iloc[0] > prev_hour):  # window doesn't cover the whole prior hour
        return None
    seg = c5.loc[m]
    hi, lo, cl = float(seg["high"].max()), float(seg["low"].min()), float(seg["close"].iloc[-1])
    p = (hi + lo + cl) / 3.0
    bc = (hi + lo) / 2.0
    tc = 2.0 * p - bc
    return p, min(bc, tc), max(bc, tc)


def _swing_levels(c5: pd.DataFrame, cfg: AkRoxxConfig) -> tuple[list[float], list[float]]:
    hi = pivot_high(c5["high"], cfg.pivot_left, cfg.pivot_right).dropna()
    lo = pivot_low(c5["low"], cfg.pivot_left, cfg.pivot_right).dropna()
    return [float(x) for x in hi.iloc[-cfg.sr_keep :]], [float(x) for x in lo.iloc[-cfg.sr_keep :]]


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: AkRoxxConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ak_roxx_pro", "asset": symbol, "event": "none"}

    need = max(cfg.ma_slow + cfg.slope_lookback + 2, cfg.atr_len + 2, cfg.pivot_left + cfg.pivot_right + 2)
    if len(candles) < need:
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    atr_val = atr_last(c5, cfg.atr_len)
    pos = st["position"]

    # ---- manage an open position ----
    if pos:
        side = pos["side"]
        reason = None
        if trail_reason := update_and_check(pos, price, cfg.trail):
            reason = trail_reason
        elif _target_hit(pos, price, cfg):
            reason = f"1:{cfg.rr:g} target"
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
        ev.update(event="wait", reason="EMA ribbon not stacked/sloping")
        return st, ev

    cpr = _prev_hour_cpr(c5)
    if cfg.require_beyond_cpr:
        if not cpr:
            ev.update(event="wait", reason="no prior 1H CPR yet")
            return st, ev
        _p, bc, tc = cpr
        if trend == 1 and price <= tc:
            ev.update(event="wait", reason="NO TRADE ZONE — long but price not above 1H TC")
            return st, ev
        if trend == -1 and price >= bc:
            ev.update(event="wait", reason="NO TRADE ZONE — short but price not below 1H BC")
            return st, ev

    res, sup = _swing_levels(c5, cfg)
    band = cfg.near_pct / 100.0 * price
    if trend == 1 and any(0.0 <= r - price <= band for r in res):
        ev.update(event="wait", reason="long blocked — price into resistance")
        return st, ev
    if trend == -1 and any(0.0 <= price - lvl <= band for lvl in sup):
        ev.update(event="wait", reason="short blocked — price into support")
        return st, ev

    rng = float(c5["high"].iloc[-1] - c5["low"].iloc[-1])
    if rng > cfg.big_candle_atr * atr_val:
        ev.update(event="wait", reason="signal candle oversized — waiting for a retrace")
        return st, ev

    want = "long" if trend == 1 else "short"
    st["position"] = {"side": want, "entry_price": price, "entry_time": ts}
    ev.update(
        event="enter",
        side=want,
        price=price,
        reason=f"21/34/55 ribbon stacked {'up' if trend == 1 else 'down'}, beyond 1H CPR",
        ts=ts,
    )
    return st, ev


def _target_hit(pos: dict[str, Any], price: float, cfg: AkRoxxConfig) -> bool:
    """Fixed 1:rr target in P&L terms — risk is the trail's initial stop."""
    cur = pnl_pct(float(pos["entry_price"]), price, pos["side"], cfg.trail.leverage)
    return cur >= cfg.rr * cfg.trail.stop_pnl_pct


if __name__ == "__main__":  # self-check — rally that stacks the ribbon and clears the prior-hour CPR
    import numpy as np

    n = 900
    idx = pd.date_range("2026-09-06 00:00", periods=n, freq="5min", tz="UTC")
    px = np.concatenate([
        100.0 + np.sin(np.linspace(0, 6, 360)) * 2,   # base — sets early hours' CPR
        np.linspace(100.0, 130.0, n - 360),           # clean rally through it
    ])
    df = pd.DataFrame(
        {"datetime": idx, "open": px, "high": px + 0.3, "low": px - 0.3, "close": px,
         "volume": [10.0] * n}
    )
    cfg = AkRoxxConfig(slope_lookback=2)
    fired = None
    state = None
    for i in range(400, n):
        state, evt = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if evt["event"] == "enter":
            fired = evt
            break
    assert fired and fired["side"] == "long", fired
    print("crypto.strategies.ak_roxx_pro self-check ok —", fired["reason"])
