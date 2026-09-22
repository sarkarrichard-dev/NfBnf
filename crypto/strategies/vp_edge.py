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
    vp_lookback: int = 96  # bars in the profile (~1 day of 15m)
    vp_bins: int = 30
    value_area_pct: float = 0.70
    edge_buffer_pct: float = (
        0.6  # fade only if price is <= this % *beyond* the edge (else it's a breakout)
    )
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None, "_prof_n": 0, "_prof": None}


_PROFILE_STRIDE = 3  # the profile barely moves bar-to-bar; recompute every N bars


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: VpEdgeConfig,
    live_price: float | None = None,
    live_range: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "vp_edge", "asset": symbol, "event": "none"}

    if len(candles) < cfg.vp_lookback + 2:
        ev.update(event="wait", reason=f"need {cfg.vp_lookback + 2} bars, have {len(candles)}")
        return st, ev

    n = len(candles)
    cached = st.get("_prof")
    if not cached or n - int(st.get("_prof_n") or 0) >= _PROFILE_STRIDE:
        pr = profile(candles.iloc[-cfg.vp_lookback :], bins=cfg.vp_bins, va_pct=cfg.value_area_pct)
        cached = (
            None
            if pr is None
            else {
                "poc": pr.poc,
                "vah": pr.vah,
                "val": pr.val,
                "balanced": pr.balanced,
            }
        )
        st["_prof"], st["_prof_n"] = cached, n
    if not cached:
        ev.update(event="wait", reason="profile not ready")
        return st, ev
    poc, vah, val, balanced = cached["poc"], cached["vah"], cached["val"], cached["balanced"]

    price = float(candles["close"].iloc[-1])
    ts = str(candles["datetime"].iloc[-1])
    # trailing stop/target reacts to the live mark, not just the last closed
    # candle — see crypto/strategies/cpr_trend.py for why.
    trail_price = live_price if live_price is not None else price
    # and the candle's real low/high (2026-09-22) catches a spike-and-reverse
    # that happened between two scans — a single live price can still miss it.
    trail_low, trail_high = live_range if live_range is not None else (trail_price, trail_price)
    buf = cfg.edge_buffer_pct / 100.0

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail, low=trail_low, high=trail_high)
        if not reason:
            if side == "long" and price >= poc:
                reason = "reached POC"
            elif side == "short" and price <= poc:
                reason = "reached POC"
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

    if not balanced:
        ev.update(event="wait", reason="profile not balanced (D-shape only)")
        return st, ev

    # fade the edge back to POC — but only if price is at/just past it, not
    # running away (a breakout is not a mean-reversion setup)
    if val * (1 - buf) <= price <= val:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="long",
            price=price,
            reason=f"long at value-area low {val:,.1f} (POC {poc:,.1f})",
            ts=ts,
        )
    elif vah <= price <= vah * (1 + buf):
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason=f"short at value-area high {vah:,.1f} (POC {poc:,.1f})",
            ts=ts,
        )
    else:
        ev.update(event="wait", reason="price inside value area / too far past the edge")
    return st, ev


if __name__ == "__main__":  # self-check — mean-reverting (Gaussian-clustered) range
    import numpy as np

    rng = np.random.default_rng(3)
    n = 400
    px = np.empty(n)
    px[0] = 100.0
    for i in range(1, n):  # Ornstein-Uhlenbeck around 100
        px[i] = px[i - 1] + 0.15 * (100.0 - px[i - 1]) + rng.normal(0, 0.35)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC"),
            "open": px,
            "high": px + 0.3,
            "low": px - 0.3,
            "close": px,
            "volume": np.abs(rng.normal(10, 2, n)),
        }
    )
    cfg = VpEdgeConfig(vp_lookback=100, vp_bins=24, edge_buffer_pct=1.0)
    state, saw = None, {"enter": 0, "exit": 0}
    for i in range(102, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] in saw:
            saw[ev["event"]] += 1
    assert saw["enter"] >= 1 and saw["exit"] >= 1, saw
    print("crypto.strategies.vp_edge self-check ok —", saw)
