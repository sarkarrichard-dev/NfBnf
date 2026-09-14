"""Funding-rate squeeze — a contrarian crypto strategy, not another trend-follower.

Every other crypto lane (ny_n_break, ichimoku, ak_roxx_pro, cpr_trend) is the
same bet: price is already moving, follow it. All four are flat or losing
(see crypto/strategies/RESULTS.md). This is the opposite bet: when a perp's
funding rate is unusually extreme *relative to its own recent history* (a
"fair" rate differs per coin), one side of the market is unusually crowded
and leveraged, and a stall is often enough to force that crowd to unwind —
a squeeze. This fades the crowded side once price shows the first sign the
move has stalled.

Untested — no forward record. See scripts/backtest_funding_squeeze.py before
this goes anywhere near crypto/lanes.py. Needs a ``funding_rate`` column on
the candles frame (crypto.backtest merges it in from
crypto.delta.market_data.funding_rate_history — an undocumented Delta feed,
see that function's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class FundingSqueezeConfig:
    lookback: int = 24 * 14  # bars for the funding rate's own rolling mean/std (14 days of 1h bars)
    z_threshold: float = 2.0  # std-devs from its own recent average that counts as "crowded"
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: FundingSqueezeConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "funding_squeeze", "asset": symbol, "event": "none"}

    need = cfg.lookback + 2
    if "funding_rate" not in candles.columns or len(candles) < need:
        ev.update(event="wait", reason=f"need {need} bars with funding_rate, have {len(candles)}")
        return st, ev

    funding = candles["funding_rate"].astype(float)
    mean = funding.rolling(cfg.lookback).mean().iloc[-1]
    std = funding.rolling(cfg.lookback).std().iloc[-1]
    now_rate = float(funding.iloc[-1])
    if pd.isna(mean) or pd.isna(std) or std == 0:
        ev.update(event="wait", reason="funding history not ready")
        return st, ev
    z = (now_rate - mean) / std

    row = candles.iloc[-1]
    price = float(row["close"])
    ts = str(candles["datetime"].iloc[-1])
    red_bar = float(row["close"]) < float(row["open"])
    green_bar = float(row["close"]) > float(row["open"])

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, price, cfg.trail)
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if z >= cfg.z_threshold and red_bar:
        # crowded longs paying an unusually big premium, and the bar just stalled
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason=f"funding z={z:.2f} (crowded longs) + stall bar",
            ts=ts,
        )
    elif z <= -cfg.z_threshold and green_bar:
        # crowded shorts being paid an unusually big premium, and the bar just stalled
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="long",
            price=price,
            reason=f"funding z={z:.2f} (crowded shorts) + stall bar",
            ts=ts,
        )
    else:
        ev.update(event="wait", reason=f"funding z={z:.2f}, no stall/threshold")
    return st, ev


if __name__ == "__main__":  # self-check — no network
    n = 24 * 15
    # flat funding for the whole lookback, then one bar spikes to +5 std devs
    # while price prints a red (down) bar -> should fire a short
    rates = [0.0003] * (n - 1) + [0.02]
    closes = [100.0] * (n - 1) + [99.0]
    opens = [100.0] * (n - 1) + [100.5]
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-08-01", periods=n, freq="1h", tz="UTC"),
            "open": opens,
            "high": [max(o, c) + 0.1 for o, c in zip(opens, closes)],
            "low": [min(o, c) - 0.1 for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [5.0] * n,
            "funding_rate": rates,
        }
    )
    cfg = FundingSqueezeConfig(lookback=24 * 14, z_threshold=2.0)
    state, ev = step("BTCUSD", df, state=None, cfg=cfg)
    assert ev["event"] == "enter" and ev["side"] == "short", ev

    # missing funding_rate column -> waits, never crashes
    state2, ev2 = step("BTCUSD", df.drop(columns=["funding_rate"]), state=None, cfg=cfg)
    assert ev2["event"] == "wait"

    # an open position exits through the normal trailing stop, same as every other lane
    st_open = {"position": {"side": "short", "entry_price": 100.0, "entry_time": "t0"}}
    df_stop = df.copy()
    df_stop.loc[df_stop.index[-1], "close"] = 130.0  # price ran hard against the short
    _, ev3 = step("BTCUSD", df_stop, state=st_open, cfg=cfg)
    assert ev3["event"] == "exit" and "stop" in ev3["reason"], ev3
    print("crypto.strategies.funding_squeeze self-check ok")
