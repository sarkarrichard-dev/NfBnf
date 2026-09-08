"""Candle-Renko — 5-minute candlestick entries, 15-minute Supertrend trend, a
Renko brick-direction agreement gate.

Richard's spec (2026-09-08): *"candlestick patterns in 5 minute time frame for
trade and 15 mins for trend and supertrend and renko."*

- **Trend (15m):** Supertrend direction on the 15-minute frame, resampled from
  the 5m candles the lane already fetches. Longs only while 15m Supertrend is
  bullish, shorts only while bearish.
- **Confirmation (5m Renko):** the last ATR-sized Renko brick must point the
  same way as the trade.
- **Trigger (5m candlestick):** a bullish reversal bar (engulfing / hammer) for
  longs, a bearish one (engulfing / shooting-star) for shorts, on the last
  closed 5m candle.
- **Exit:** the shared P&L trailing engine, or the 15m Supertrend flipping
  against the position.

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame; 15m is
derived inside. Pure: returns ``(state, event)``. The lane owns sizing /
journalling / Telegram, same as the other simple strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from crypto.strategies.indicators import atr_last, supertrend_dir
from crypto.strategies.renko import brick_dir
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class CandleRenkoConfig:
    atr_len: int = 14
    renko_atr_mult: float = 1.0
    st_period: int = 10
    st_mult: float = 3.0
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _resample_15m(c5: pd.DataFrame) -> pd.DataFrame:
    """5m OHLCV → 15m, numpy segment-reduce (pandas ``.resample`` is ~50× slower
    here and this runs once per replayed bar)."""
    idx = pd.DatetimeIndex(pd.to_datetime(c5["datetime"]))
    o, h, low, c, v = (
        c5[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")
    )
    b = idx.floor("15min")  # 15-min bucket per bar
    starts = np.concatenate(([0], np.flatnonzero(b.to_numpy()[1:] != b.to_numpy()[:-1]) + 1))
    dt = idx.to_numpy()
    ends = np.concatenate((starts[1:], [len(dt)])) - 1
    return pd.DataFrame(
        {
            "datetime": dt[starts],
            "open": o[starts],
            "high": np.maximum.reduceat(h, starts),
            "low": np.minimum.reduceat(low, starts),
            "close": c[ends],
            "volume": np.add.reduceat(v, starts),
        }
    )


def _trend_dir(c5: pd.DataFrame, cfg: CandleRenkoConfig, st: dict[str, Any]) -> int:
    """15m Supertrend direction (+1/-1/0), cached in ``st`` per 15-minute bucket.

    The 15m frame and its Supertrend only change when a new 15m bar closes, so
    within a bucket (three 5m bars) the answer is reused — this is the hot path
    when the walk-forward optimiser replays thousands of sliding windows."""
    bucket = (
        f"{pd.Timestamp(c5['datetime'].iloc[-1]).floor('15min')}"
        f"|{cfg.st_period}|{cfg.st_mult}"
    )
    if st.get("_trend_bucket") == bucket:
        return int(st.get("_trend_dir") or 0)
    d = supertrend_dir(_resample_15m(c5), cfg.st_period, cfg.st_mult)
    st["_trend_bucket"], st["_trend_dir"] = bucket, d
    return d


def _pattern(c5: pd.DataFrame) -> int:
    """+1 bullish / -1 bearish / 0 from the last closed 5m bar."""
    if len(c5) < 2:
        return 0
    o, h, low, c = (float(c5[k].iloc[-1]) for k in ("open", "high", "low", "close"))
    po, pc = float(c5["open"].iloc[-2]), float(c5["close"].iloc[-2])
    body = abs(c - o)
    rng = (h - low) or 1e-9
    upper = h - max(o, c)
    lower = min(o, c) - low
    bull = c >= o
    if bull and pc < po and c > po and o <= pc:  # bullish engulfing
        return 1
    if not bull and pc > po and c < po and o >= pc:  # bearish engulfing
        return -1
    if lower >= 2 * body and upper <= body and body / rng < 0.4:  # hammer
        return 1
    if upper >= 2 * body and lower <= body and body / rng < 0.4:  # shooting star
        return -1
    return 0


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: CandleRenkoConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "candle_renko", "asset": symbol, "event": "none"}

    if len(candles) < cfg.atr_len + 6:
        ev["event"] = "wait"
        ev["reason"] = "not enough 5m history"
        return st, ev

    c5 = candles.reset_index(drop=True)
    price = float(c5["close"].iloc[-1])
    ts = str(c5["datetime"].iloc[-1])

    trend = _trend_dir(c5, cfg, st)

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = None
        if trail_reason := update_and_check(pos, price, cfg.trail):
            reason = trail_reason
        elif (side == "long" and trend < 0) or (side == "short" and trend > 0):
            reason = "15m supertrend flip"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if trend == 0:
        ev.update(event="wait", reason="15m supertrend warming up")
        return st, ev

    brick = cfg.renko_atr_mult * atr_last(c5, cfg.atr_len)
    renko = brick_dir(c5["close"], brick)
    pat = _pattern(c5)

    if pat > 0 and trend > 0 and renko > 0:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(event="enter", side="long", price=price, ts=ts,
                  reason="bullish 5m bar, 15m ST up, renko up")
    elif pat < 0 and trend < 0 and renko < 0:
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(event="enter", side="short", price=price, ts=ts,
                  reason="bearish 5m bar, 15m ST down, renko down")
    else:
        ev.update(event="wait", reason="no aligned 5m pattern")
    return st, ev


if __name__ == "__main__":  # self-check — a trend up then a flip down
    import numpy as np

    rng = np.random.default_rng(0)
    steps = np.concatenate([rng.normal(0.4, 1.1, 300), rng.normal(-0.6, 1.1, 240)])
    n = len(steps)
    close = 100 + np.cumsum(steps)
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1]  # each bar opens at the prior close
    hi = np.maximum(open_, close) + np.abs(rng.normal(0.3, 0.3, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0.3, 0.3, n))
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC"),
            "open": open_, "high": hi, "low": lo, "close": close,
            "volume": np.abs(rng.normal(10, 2, n)),
        }
    )
    state, saw = None, {"enter": 0, "exit": 0}
    for i in range(40, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=CandleRenkoConfig())
        if ev["event"] in saw:
            saw[ev["event"]] += 1
    assert saw["enter"] >= 1 and saw["exit"] >= 1, saw
    print("crypto.strategies.candle_renko self-check ok —", saw)
