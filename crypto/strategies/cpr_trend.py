"""CPR + EMA + Supertrend directional trend-follow, brought to crypto.

Richard, 2026-09-14: "can we use the same strategies that's working in the
Indian market for the crypto" — this is the same signal already live on the
Indian indices, MCX commodities, and 20 NSE stock futures
(``index_ai.strategies.futures.engine``), reused as-is rather than
reinvented. It runs alongside the existing crypto strategies (NY N-Break,
Ichimoku, AK Roxx Pro), not instead of them, and gets tracked separately by
the per-(strategy, instrument) scorecard — the point is to find out
honestly whether it holds up on crypto, not to assume it does because it's
ahead on a handful of Indian trades.

Two things differ from the Indian/commodity callers of the same engine:
  * "previous day" for the CPR read is the prior UTC calendar day (crypto
    has no session; this mirrors the Ichimoku lane's own day anchor —
    ``crypto.session`` — rather than an IST trading-day close).
  * Exits reuse crypto's own P&L-based trailing stop
    (``crypto.strategies.trailing``), not the Indian point-based stops —
    every other crypto strategy is judged on percent-of-margin P&L, and
    this one should be too, for a fair per-strategy comparison.

Pure: ``step`` reads the candles + a state dict and returns ``(state,
event)``. The lane owns sizing and journaling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.trailing import TrailConfig, update_and_check
from index_ai.strategies.futures.config import FuturesConfig
from index_ai.strategies.futures.engine import FLAT, entry_trigger, trend_read


@dataclass(frozen=True)
class CprTrendConfig:
    # same defaults already proven across NIFTY/BANKNIFTY/SENSEX, MCX, and the
    # 20-stock futures universe — not re-guessed for crypto.
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21
    trend_st_period: int = 10
    trend_st_mult: float = 3.0
    trend_min_bars: int = 30
    entry_ema: int = 9
    entry_min_bars: int = 12
    max_extension_pct: float = 0.30
    trail: TrailConfig = field(default_factory=TrailConfig)

    def as_futures_config(self, symbol: str) -> FuturesConfig:
        return FuturesConfig(
            key=symbol,
            lot_size=1,
            trend_ema_fast=self.trend_ema_fast,
            trend_ema_slow=self.trend_ema_slow,
            trend_st_period=self.trend_st_period,
            trend_st_mult=self.trend_st_mult,
            trend_min_bars=self.trend_min_bars,
            entry_ema=self.entry_ema,
            entry_min_bars=self.entry_min_bars,
            max_extension_pct=self.max_extension_pct,
        )


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _last_two_days(c15: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """(previous_day, today) 15m frames, split on the UTC calendar date —
    None if there isn't a full previous day yet."""
    groups = c15.groupby(c15["datetime"].dt.date)
    keys = sorted(groups.groups)
    if len(keys) < 2:
        return None
    return (
        groups.get_group(keys[-2]).reset_index(drop=True),
        groups.get_group(keys[-1]).reset_index(drop=True),
    )


def step(
    symbol: str,
    c5: pd.DataFrame,
    c15: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: CprTrendConfig,
    live_price: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "cpr_trend", "asset": symbol, "event": "none"}

    if c5.empty or c15.empty:
        ev.update(event="wait", reason="no candle history")
        return st, ev

    price = float(c5["close"].iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    # the trailing stop/target must react to the live mark, not just the last
    # closed 5m candle — otherwise a fast swing inside the candle only gets
    # noticed up to 5 minutes late (2026-09-21: SOL gave back +18%/+37% peak
    # P&L before the candle-close check even saw it move).
    trail_price = live_price if live_price is not None else price

    split = _last_two_days(c15)
    if split is None:
        ev.update(event="wait", reason="not enough 15m history for a previous day")
        return st, ev
    prev15, today15 = split

    fcfg = cfg.as_futures_config(symbol)
    tr = trend_read(pd.concat([prev15, today15], ignore_index=True), prev15, fcfg)

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail)
        if not reason:
            d = 1 if side == "long" else -1
            if tr.direction not in (d, FLAT):
                reason = "trend flip"
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

    if tr.direction == FLAT:
        ev.update(event="wait", reason="no trend")
        return st, ev
    fired, why = entry_trigger(c5, tr.direction, fcfg)
    if not fired:
        ev.update(event="wait", reason=why)
        return st, ev

    side = "long" if tr.direction == 1 else "short"
    st["position"] = {"side": side, "entry_price": price, "entry_time": ts}
    ev.update(event="enter", side=side, price=price, reason=f"{tr.reason} — {why}", ts=ts)
    return st, ev


def _synthetic(start: str, n: int, freq: str, base: float, step_per_bar: float) -> pd.DataFrame:
    closes = [base + i * step_per_bar for i in range(n)]
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
            "open": closes,
            "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


if __name__ == "__main__":  # self-check — wiring correctness, not signal precision
    prev15 = _synthetic("2026-09-10", 96, "15min", 100.0, 0.0)  # flat prior day
    today15_up = _synthetic(
        "2026-09-11", 96, "15min", 110.0, 0.05
    )  # clearly above prev day's range
    c15_up = pd.concat([prev15, today15_up], ignore_index=True)
    cfg = CprTrendConfig()
    fcfg = cfg.as_futures_config("BTCUSD")

    split = _last_two_days(c15_up)
    assert split is not None
    tr = trend_read(pd.concat(split, ignore_index=True), split[0], fcfg)
    assert tr.direction == 1, tr.reason  # the synthetic setup must actually read bullish

    c5 = _synthetic("2026-09-11", 250, "5min", 110.0, 0.02)
    state, ev = step("BTCUSD", c5, c15_up, state=None, cfg=cfg)
    assert ev["event"] in ("enter", "wait"), ev
    if ev["event"] == "enter":
        assert ev["side"] == "long"
        assert state["position"] is not None

    # an open position must exit once the 15m read turns clearly bearish
    open_state = {"position": {"side": "long", "entry_price": float(c5["close"].iloc[-1])}}
    today15_down = _synthetic("2026-09-11", 96, "15min", 90.0, -0.05)
    _, ev2 = step(
        "BTCUSD",
        c5,
        pd.concat([prev15, today15_down], ignore_index=True),
        state=open_state,
        cfg=cfg,
    )
    assert ev2["event"] == "exit", ev2

    # empty candles never raise
    empty = pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    _, ev3 = step("BTCUSD", empty, empty, state=None, cfg=cfg)
    assert ev3["event"] == "wait"
    print("crypto.strategies.cpr_trend self-check ok - trend", tr.reason)
