"""EMA Jaguar — a fast/slow EMA crossover on the 5-minute chart.

From "A Simple 5-Minute EMA Strategy For Crypto Trading": EMA(13) / EMA(34) on
close. Long when the fast EMA crosses above the slow, short when it crosses
below, entered on the closed candle. Exit on the opposite cross; the shared
``crypto/strategies/trailing.py`` P&L trailing stop is the risk control.

The 13/34 periods are the video defaults — ``crypto/ml/optimize.py`` walk-forward
tunes ``fast`` / ``slow`` against real candle history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import cross_dir, ema
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class EmaJaguarConfig:
    fast: int = 13
    slow: int = 34
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: EmaJaguarConfig,
    live_price: float | None = None,
    live_range: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ema_jaguar", "asset": symbol, "event": "none"}

    need = max(cfg.fast, cfg.slow) + 3
    if len(candles) < need:
        ev.update(event="wait", reason=f"need {need} bars, have {len(candles)}")
        return st, ev

    close = candles["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(candles["datetime"].iloc[-1])
    # trailing stop/target reacts to the live mark, not just the last closed
    # candle — see crypto/strategies/cpr_trend.py for why.
    trail_price = live_price if live_price is not None else price
    # and the candle's real low/high (2026-09-22) catches a spike-and-reverse
    # that happened between two scans — a single live price can still miss it.
    trail_low, trail_high = live_range if live_range is not None else (trail_price, trail_price)
    f, s = ema(close, cfg.fast), ema(close, cfg.slow)
    xdir = cross_dir(f, s)

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail, low=trail_low, high=trail_high)
        if not reason:
            if side == "long" and xdir < 0:
                reason = f"EMA{cfg.fast}/{cfg.slow} cross down"
            elif side == "short" and xdir > 0:
                reason = f"EMA{cfg.fast}/{cfg.slow} cross up"
        if reason:
            st["position"] = None
            ev.update(
                event="exit",
                side=side,
                price=pos.get("trail_exit_price", trail_price),
                reason=reason,
                ts=ts,
                peak_pnl_pct=pos.get("peak_pnl_pct"),
                trail_stop_pnl_pct=pos.get("trail_stop_pnl_pct"),
            )
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if xdir > 0:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="long",
            price=price,
            reason=f"EMA{cfg.fast} crossed above EMA{cfg.slow}",
            ts=ts,
        )
    elif xdir < 0:
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason=f"EMA{cfg.fast} crossed below EMA{cfg.slow}",
            ts=ts,
        )
    else:
        ev.update(event="wait", reason="no EMA cross")
    return st, ev


if __name__ == "__main__":  # self-check — up, down, up: crosses both ways
    closes = (
        [100 + 0.5 * i for i in range(40)]  # rise
        + [120 - 0.7 * i for i in range(40)]  # fall → cross down (enter short)
        + [92 + 0.6 * i for i in range(40)]  # rise → cross up (exit short, enter long)
    )
    n = len(closes)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [5.0] * n,
        }
    )
    cfg = EmaJaguarConfig(fast=5, slow=13)
    state, saw = None, {"enter": 0, "exit": 0}
    for i in range(16, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] in saw:
            saw[ev["event"]] += 1
    assert saw["enter"] >= 2 and saw["exit"] >= 1, saw
    print("crypto.strategies.ema_jaguar self-check ok —", saw)
