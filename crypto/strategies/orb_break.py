"""ORB (Opening Range Breakout) for crypto.

Richard, 2026-09-22: shared an Instagram reel of someone building an ORB bot
for ES futures — watch the first 30 minutes of the NY cash open, then trade
the breakout of that range, stop at the opposite side (or a fixed distance),
target as a multiple of the risk. Crypto has no equivalent "market open," so
the opening range anchors to the same 18:00 IST NY-window start already
established for ``ny_n_break`` (the "6 PM strategy") — the closest existing
analog in this codebase, not a guess. Exit reuses the shared crypto P&L trail
(Richard, 2026-09-22: every crypto strategy's exit should be standard) rather
than a separate R-multiple target, so this is judged the same way as every
other strategy here, not on its own bespoke terms.

Mechanics:
  * range = the high/low of the first ``orb_minutes`` (default 30) after
    ``orb_start`` (default 18:00 IST), built bar-by-bar on 5-minute candles.
  * entry = a 5m candle CLOSING above the range high (long) or below the
    range low (short), once the range is set. One trade per side per day —
    real ORB discipline; the range doesn't re-arm after a stop-out.
  * exit = the shared P&L trailing stop/profit
    (``crypto/strategies/trailing.py``).

Untested until ``scripts/backtest_orb_crypto.py`` says otherwise — the prior
on any strategy pulled from a social-media video is "probably no edge" until
proven on real Delta prices with real fees, same as everything else here.

Pure: ``step`` reads the candles + a state dict and returns ``(state,
event)``. The lane owns sizing and journaling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from crypto.session import parse_hhmm, session_date_for
from crypto.strategies.trailing import TrailConfig, update_and_check
from index_ai.market_clock import IST


@dataclass(frozen=True)
class OrbBreakConfig:
    orb_start: str = "18:00"  # IST anchor for the opening range — matches ny_n_break's NY window
    orb_minutes: int = 30  # opening-range duration
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {
        "range_day": None,
        "range_high": None,
        "range_low": None,
        "long_done": False,
        "short_done": False,
        "position": None,
    }


def _to_ist(raw) -> datetime:
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(IST).to_pydatetime()


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: OrbBreakConfig,
    live_price: float | None = None,
    live_range: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "orb_break", "asset": symbol, "event": "none"}

    if candles.empty:
        ev.update(event="wait", reason="no candle history")
        return st, ev

    price = float(candles["close"].iloc[-1])
    ts_raw = candles["datetime"].iloc[-1]
    ts = str(ts_raw)
    now_ist = _to_ist(ts_raw)
    # trailing stop/target reacts to the live mark and the candle's real
    # low/high, not just this candle's close — see crypto/strategies/
    # cpr_trend.py and crypto/lanes.py::_candle_range for why.
    trail_price = live_price if live_price is not None else price
    trail_low, trail_high = live_range if live_range is not None else (trail_price, trail_price)

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail, low=trail_low, high=trail_high)
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

    day = session_date_for(cfg.orb_start, now_ist)
    if st.get("range_day") != day:
        st.update(range_day=day, range_high=None, range_low=None, long_done=False, short_done=False)

    orb_start_t = parse_hhmm(cfg.orb_start)
    start_dt = datetime.combine(date.fromisoformat(day), orb_start_t, tzinfo=IST)
    end_dt = start_dt + timedelta(minutes=cfg.orb_minutes)

    if start_dt <= now_ist < end_dt:
        bar_hi = float(candles["high"].iloc[-1])
        bar_lo = float(candles["low"].iloc[-1])
        st["range_high"] = bar_hi if st["range_high"] is None else max(st["range_high"], bar_hi)
        st["range_low"] = bar_lo if st["range_low"] is None else min(st["range_low"], bar_lo)
        ev.update(event="wait", reason=f"building the {cfg.orb_minutes}m opening range")
        return st, ev

    rng_high, rng_low = st["range_high"], st["range_low"]
    if rng_high is None or rng_low is None:
        ev.update(event="wait", reason="no opening range recorded yet")
        return st, ev

    if not st["long_done"] and price > rng_high:
        st["long_done"] = True
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="long",
            price=price,
            ts=ts,
            reason=f"ORB long — closed above the {cfg.orb_minutes}m range high {rng_high:.4g}",
        )
        return st, ev
    if not st["short_done"] and price < rng_low:
        st["short_done"] = True
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            ts=ts,
            reason=f"ORB short — closed below the {cfg.orb_minutes}m range low {rng_low:.4g}",
        )
        return st, ev

    ev.update(event="wait", reason="inside the opening range, no break yet")
    return st, ev


if __name__ == "__main__":  # self-check — wiring correctness, not signal precision
    # a flat pre-range chop, then a range-building segment, then a clean
    # breakout above the range high
    base = "2026-09-07 12:00"
    idx = pd.date_range(base, periods=200, freq="5min", tz="UTC")  # 12:00 UTC = 17:30 IST
    px = [100.0] * len(idx)
    df = pd.DataFrame(
        {
            "datetime": idx,
            "open": px,
            "high": [p + 0.2 for p in px],
            "low": [p - 0.2 for p in px],
            "close": px,
            "volume": [10.0] * len(idx),
        }
    )
    # the range window (18:00-18:30 IST = 12:30-13:00 UTC) gets a wider bar
    range_mask = (df["datetime"] >= "2026-09-07 12:30") & (df["datetime"] < "2026-09-07 13:00")
    df.loc[range_mask, ["high", "low"]] = [101.0, 99.0]
    # after the range: a clean breakout close above 101
    post_mask = df["datetime"] >= "2026-09-07 13:05"
    df.loc[post_mask, ["open", "high", "low", "close"]] = [102.0, 102.3, 101.7, 102.0]

    cfg = OrbBreakConfig()
    state, entered = None, None
    for i in range(1, len(df)):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] == "enter":
            entered = ev
            break
    assert entered and entered["side"] == "long", entered
    assert state["range_high"] == 101.0 and state["range_low"] == 99.0, state

    # a second breakout attempt the same day, same side, must NOT re-enter
    # (one trade per side per day) — force the position flat and re-check
    state["position"] = None
    _, ev2 = step("BTCUSD", df, state=state, cfg=cfg)
    assert ev2["event"] != "enter", ev2

    # empty candles never raise
    empty = pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    _, ev3 = step("BTCUSD", empty, state=None, cfg=cfg)
    assert ev3["event"] == "wait"
    print("crypto.strategies.orb_break self-check ok —", entered["reason"])
