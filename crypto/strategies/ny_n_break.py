"""NY N-Break — the '6 PM' strategy, ported from ``crypto/strategies/ny_n_break.pine``.

5-minute entries inside the IST window, 15-minute opposite-N exit, max 3 trades
per session. Pure: ``step`` reads the candles + a state dict and returns
``(state, event)``. The lane owns sizing, journaling and Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import (
    anchored_vwap,
    crossed_over,
    crossed_under,
    ema,
    pivot_high,
    pivot_low,
)
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class NBreakConfig:
    ema_len: int = 25
    swing_left: int = 5
    swing_right: int = 2
    exit_swing_left: int = 5
    exit_swing_right: int = 2
    max_trades_per_session: int = 3
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {
        "position": None,
        "long_lvl": None,
        "long_lvl_ts": None,
        "short_lvl": None,
        "short_lvl_ts": None,
        "trades_today": 0,
        "session_date": None,
    }


def _last_confirmed(series: pd.Series) -> float | None:
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None


def _arm(pivots: pd.Series, on_side: pd.Series, ts_series: pd.Series, r: int,
         cur_lvl: float | None, cur_ts: str | None) -> tuple[float | None, str | None]:
    """Arm from the most recent confirmed swing whose pivot bar was on-side.
    Returns (level, pivot_bar_timestamp) unchanged if nothing new qualifies —
    so a level already traded (level cleared, ts kept) is not re-armed until a
    fresher swing forms."""
    lv = pivots.last_valid_index()
    if lv is None:
        return cur_lvl, cur_ts
    pivot_pos = pivots.index.get_loc(lv) - r
    if pivot_pos < 0 or pivot_pos >= len(on_side) or not bool(on_side.iloc[pivot_pos]):
        return cur_lvl, cur_ts
    pivot_ts = str(ts_series.iloc[pivot_pos])
    if pivot_ts == cur_ts:
        return cur_lvl, cur_ts
    return float(pivots.loc[lv]), pivot_ts


def step(
    symbol: str,
    c5: pd.DataFrame,
    c15: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: NBreakConfig,
    in_session: bool,
    session_date: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ny_n_break", "asset": symbol, "event": "none"}

    if len(c5) < max(cfg.ema_len, cfg.swing_left + cfg.swing_right) + 3:
        ev["event"] = "wait"
        ev["reason"] = "not enough 5m history"
        return st, ev

    # new session → reset arm levels + counter
    if st["session_date"] != session_date:
        st.update(
            long_lvl=None, long_lvl_ts=None, short_lvl=None, short_lvl_ts=None,
            trades_today=0, session_date=session_date,
        )

    close5 = c5["close"].astype(float)
    price = float(close5.iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    ema_v = ema(close5, cfg.ema_len)
    vwap_v = anchored_vwap(c5)

    # arm from the most recent confirmed swing whose pivot bar was on the right
    # side of EMA + VWAP
    ph = pivot_high(close5, cfg.swing_left, cfg.swing_right)
    pl = pivot_low(close5, cfg.swing_left, cfg.swing_right)
    above = _side_series(c5, ema_v, vwap_v, upper=True)
    below = _side_series(c5, ema_v, vwap_v, upper=False)
    tss = c5["datetime"].astype(str).reset_index(drop=True)
    st["long_lvl"], st["long_lvl_ts"] = _arm(
        ph.reset_index(drop=True), above.reset_index(drop=True), tss,
        cfg.swing_right, st["long_lvl"], st["long_lvl_ts"],
    )
    st["short_lvl"], st["short_lvl_ts"] = _arm(
        pl.reset_index(drop=True), below.reset_index(drop=True), tss,
        cfg.swing_right, st["short_lvl"], st["short_lvl_ts"],
    )

    pos = st["position"]

    # ---- manage an open position ----
    if pos:
        side = pos["side"]
        reason = None
        if not in_session:
            reason = "session end"
        elif (trail_reason := update_and_check(pos, price, cfg.trail)):
            reason = trail_reason
        else:
            sh15 = _last_confirmed(pivot_high(c15["close"].astype(float), cfg.exit_swing_left, cfg.exit_swing_right)) if len(c15) else None
            sl15 = _last_confirmed(pivot_low(c15["close"].astype(float), cfg.exit_swing_left, cfg.exit_swing_right)) if len(c15) else None
            c15close = c15["close"].astype(float) if len(c15) else pd.Series(dtype=float)
            if side == "long" and crossed_under(c15close, sl15):
                reason = "15m inverted-N"
            elif side == "short" and crossed_over(c15close, sh15):
                reason = "15m N"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    # ---- look for an entry ----
    if not in_session:
        ev.update(event="wait", reason="outside 6 PM window")
        return st, ev
    if st["trades_today"] >= cfg.max_trades_per_session:
        ev.update(event="wait", reason=f"max {cfg.max_trades_per_session} trades this session")
        return st, ev

    if st["long_lvl"] is not None and crossed_over(close5, st["long_lvl"]):
        lvl = st["long_lvl"]
        st.update(position={"side": "long", "entry_price": price, "entry_time": ts},
                  long_lvl=None, trades_today=st["trades_today"] + 1)
        ev.update(event="enter", side="long", price=price, reason=f"N-break over {lvl:,.1f}", ts=ts)
    elif st["short_lvl"] is not None and crossed_under(close5, st["short_lvl"]):
        lvl = st["short_lvl"]
        st.update(position={"side": "short", "entry_price": price, "entry_time": ts},
                  short_lvl=None, trades_today=st["trades_today"] + 1)
        ev.update(event="enter", side="short", price=price, reason=f"inverted-N under {lvl:,.1f}", ts=ts)
    else:
        armed = st["long_lvl"] is not None or st["short_lvl"] is not None
        ev.update(event="wait", reason="waiting for the N-break" if armed else "no setup yet")
    return st, ev


def _side_series(c5: pd.DataFrame, ema_v: pd.Series, vwap_v: pd.Series, *, upper: bool = True) -> pd.Series:
    close = c5["close"].astype(float)
    if upper:
        return (close > ema_v) & (close > vwap_v)
    return (close < ema_v) & (close < vwap_v)


if __name__ == "__main__":  # self-check — synthetic N-break
    n = 60
    # build: rise, pull back forming a swing high ~ index 40, then re-break it
    closes = list(range(100, 140)) + [138, 136, 135, 137, 139, 141, 143, 145, 147, 149] + list(range(150, 160))
    closes = closes[:n]
    df5 = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-07 18:00", periods=n, freq="5min", tz="Asia/Kolkata"),
            "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
            "close": closes, "volume": [10.0] * n,
        }
    )
    df15 = df5.iloc[::3].reset_index(drop=True)
    cfg = NBreakConfig()
    state = None
    fired = None
    for i in range(30, n):
        state, ev = step("BTCUSD", df5.iloc[: i + 1], df15, state=state, cfg=cfg,
                         in_session=True, session_date="2026-09-07")
        if ev["event"] == "enter":
            fired = ev
            break
    assert fired and fired["side"] == "long", fired
    assert state["position"] and state["trades_today"] == 1
    print("crypto.strategies.ny_n_break self-check ok —", fired["reason"])
