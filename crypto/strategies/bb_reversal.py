"""BB Reversal — Bollinger-band mean-reversion with a W / M confirmation.

From the "Bitcoin & Crypto Scalping Strategy" video: price stretches to a
Bollinger band, prints a double bottom (W) at/below the lower band → long on the
break of the peak between the two lows (the W neckline); a double top (M)
at/above the upper band → short on the break of the trough between the two highs.

5-minute chart. ``bb_len`` / ``bb_dev`` / ``swing_left`` / ``swing_right`` are
the tunables — ``crypto/ml/optimize.py`` walk-forward tunes them. The shared
P&L trailing stop is the risk control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import (
    bollinger,
    crossed_over,
    crossed_under,
    pivot_high,
    pivot_low,
)
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class BBReversalConfig:
    bb_len: int = 20
    bb_dev: float = 2.0
    swing_left: int = 3
    swing_right: int = 2
    min_bandwidth_pct: float = 0.15  # skip a dead-flat market (band width / mid, %)
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {
        "position": None,
        "long_lvl": None,
        "long_lvl_i": None,
        "short_lvl": None,
        "short_lvl_i": None,
    }


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: BBReversalConfig,
    live_price: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "bb_reversal", "asset": symbol, "event": "none"}

    need = cfg.bb_len + cfg.swing_left + cfg.swing_right + 3
    if len(candles) < need:
        ev.update(event="wait", reason=f"need {need} bars, have {len(candles)}")
        return st, ev

    close = candles["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(candles["datetime"].iloc[-1])
    # trailing stop/target reacts to the live mark, not just the last closed
    # candle — see crypto/strategies/cpr_trend.py for why.
    trail_price = live_price if live_price is not None else price
    mid, upper, lower = bollinger(close, cfg.bb_len, cfg.bb_dev)
    lo_band, up_band, mid_v = float(lower.iloc[-1]), float(upper.iloc[-1]), float(mid.iloc[-1])
    if mid_v <= 0 or (up_band - lo_band) / mid_v * 100.0 < cfg.min_bandwidth_pct:
        ev.update(event="wait", reason="bands too tight — no volatility")
        return st, ev

    pl = pivot_low(close, cfg.swing_left, cfg.swing_right).reset_index(drop=True)
    ph = pivot_high(close, cfg.swing_left, cfg.swing_right).reset_index(drop=True)
    bands_lo = lower.reset_index(drop=True)
    bands_up = upper.reset_index(drop=True)

    # W-reversal: a confirmed swing low that printed at/below the lower band →
    # arm the neckline at the highest close since that low. Enter on the break.
    li = pl.last_valid_index()
    if li is not None and li != st.get("long_lvl_i"):
        band_at = float(bands_lo.iloc[li]) if pd.notna(bands_lo.iloc[li]) else lo_band
        if float(pl.iloc[li]) <= band_at:
            st["long_lvl"] = float(close.iloc[li:].max())
            st["long_lvl_i"] = li
    hi = ph.last_valid_index()
    if hi is not None and hi != st.get("short_lvl_i"):
        band_at = float(bands_up.iloc[hi]) if pd.notna(bands_up.iloc[hi]) else up_band
        if float(ph.iloc[hi]) >= band_at:
            st["short_lvl"] = float(close.iloc[hi:].min())
            st["short_lvl_i"] = hi

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail)
        if reason:
            st["position"] = None
            ev.update(
                event="exit",
                side=side,
                price=trail_price,
                reason=reason,
                ts=ts,
                peak_pnl_pct=pos.get("peak_pnl_pct"),
                trail_stop_pnl_pct=pos.get("trail_stop_pnl_pct"),
            )
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if st["long_lvl"] is not None and crossed_over(close, st["long_lvl"]):
        lvl = st["long_lvl"]
        st.update(position={"side": "long", "entry_price": price, "entry_time": ts}, long_lvl=None)
        ev.update(
            event="enter",
            side="long",
            price=price,
            reason=f"W break over {lvl:,.1f} (lower band {lo_band:,.1f})",
            ts=ts,
        )
    elif st["short_lvl"] is not None and crossed_under(close, st["short_lvl"]):
        lvl = st["short_lvl"]
        st.update(
            position={"side": "short", "entry_price": price, "entry_time": ts}, short_lvl=None
        )
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason=f"M break under {lvl:,.1f} (upper band {up_band:,.1f})",
            ts=ts,
        )
    else:
        armed = st["long_lvl"] is not None or st["short_lvl"] is not None
        ev.update(
            event="wait", reason="waiting for the W/M break" if armed else "no band-edge setup"
        )
    return st, ev


if __name__ == "__main__":  # self-check — price pierces the lower band, bounces, breaks structure
    import numpy as np

    rng = np.random.default_rng(2)
    closes = (
        list(100 + rng.normal(0, 0.8, 20))  # choppy → real band width
        + [
            98,
            95,
            91,
            87,
            90,
            93,
            91,
            88,
            92,
            96,
            100,
            104,
        ]  # pierce band, bounce, dip, rally-through
        + [104.0] * 6
    )
    n = len(closes)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC"),
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [5.0] * n,
        }
    )
    cfg = BBReversalConfig(bb_len=14, bb_dev=2.0, swing_left=2, swing_right=1)
    state, fired = None, None
    for i in range(16, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] == "enter":
            fired = ev
            break
    assert fired and fired["side"] == "long", fired
    print("crypto.strategies.bb_reversal self-check ok —", fired["reason"])
