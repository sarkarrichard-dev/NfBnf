"""VP Edge — value-area mean-reversion on a balanced session profile.

The implementable slice of the Theta Gainers "Bitcoin & ETH intraday" video: on
a **D-shape / balanced** volume profile (market found fair value), fade the
value-area edges — short near VAH, long near VAL, target the POC. The video's
discretionary P-/b-shape continuation calls and the footprint/CVD layer are out
of scope.

15-minute chart. ``vp_lookback`` / ``vp_bins`` / ``value_area_pct`` /
``edge_buffer_pct`` are the tunables — ``crypto/ml/optimize.py`` walk-forward
tunes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.trailing import TrailConfig, update_and_check
from crypto.strategies.volprofile import profile


@dataclass(frozen=True)
class VpEdgeConfig:
    vp_lookback: int = 96          # bars in the profile (~1 day of 15m)
    vp_bins: int = 30
    value_area_pct: float = 0.70
    edge_buffer_pct: float = 0.6   # fade only if price is <= this % *beyond* the edge (else it's a breakout)
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: VpEdgeConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "vp_edge", "asset": symbol, "event": "none"}

    if len(candles) < cfg.vp_lookback + 2:
        ev.update(event="wait", reason=f"need {cfg.vp_lookback + 2} bars, have {len(candles)}")
        return st, ev

    frame = candles.iloc[-cfg.vp_lookback:]
    p = profile(frame, bins=cfg.vp_bins, va_pct=cfg.value_area_pct)
    if p is None:
        ev.update(event="wait", reason="profile not ready")
        return st, ev

    price = float(candles["close"].iloc[-1])
    ts = str(candles["datetime"].iloc[-1])
    buf = cfg.edge_buffer_pct / 100.0

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, price, cfg.trail)
        if not reason:
            if side == "long" and price >= p.poc:
                reason = "reached POC"
            elif side == "short" and price <= p.poc:
                reason = "reached POC"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if not p.balanced:
        ev.update(event="wait", reason="profile not balanced (D-shape only)")
        return st, ev

    # fade the edge back to POC — but only if price is at/just past it, not
    # running away (a breakout is not a mean-reversion setup)
    if p.val * (1 - buf) <= price <= p.val:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(event="enter", side="long", price=price,
                  reason=f"long at value-area low {p.val:,.1f} (POC {p.poc:,.1f})", ts=ts)
    elif p.vah <= price <= p.vah * (1 + buf):
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(event="enter", side="short", price=price,
                  reason=f"short at value-area high {p.vah:,.1f} (POC {p.poc:,.1f})", ts=ts)
    else:
        ev.update(event="wait", reason="price inside value area / too far past the edge")
    return st, ev


if __name__ == "__main__":  # self-check — mean-reverting (Gaussian-clustered) range
    import numpy as np

    rng = np.random.default_rng(3)
    n = 400
    px = np.empty(n)
    px[0] = 100.0
    for i in range(1, n):                       # Ornstein-Uhlenbeck around 100
        px[i] = px[i - 1] + 0.15 * (100.0 - px[i - 1]) + rng.normal(0, 0.35)
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC"),
        "open": px, "high": px + 0.3, "low": px - 0.3, "close": px,
        "volume": np.abs(rng.normal(10, 2, n)),
    })
    cfg = VpEdgeConfig(vp_lookback=100, vp_bins=24, edge_buffer_pct=1.0)
    state, saw = None, {"enter": 0, "exit": 0}
    for i in range(102, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] in saw:
            saw[ev["event"]] += 1
    assert saw["enter"] >= 1 and saw["exit"] >= 1, saw
    print("crypto.strategies.vp_edge self-check ok —", saw)
