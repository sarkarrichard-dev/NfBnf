"""RSI + EMA cross + ADX trend filter — adapted from freqtrade-strategies'
``hlhb`` (the "HLHB System", a decades-old simple forex trend system:
https://www.babypips.com/trading/forex-hlhb-system-explained).

Richard, 2026-09-15: asked to look through a list of trading GitHub repos and
pull out concrete ideas to test. Most of what's in freqtrade-strategies is
either a re-skin of indicators already on this platform (Supertrend, Bollinger,
channel breaks) or numbers clearly hyperopted to one narrow backtest window
(a stoploss of exactly -0.3211, an ROI table with four hand-tuned steps) —
not signal, curve-fit. This one's original entry logic is a genuinely
different combination nothing here uses yet: a momentum shift (RSI crossing
its midpoint) confirmed by a fast/slow EMA cross, gated by ADX so it only
fires when a real trend actually exists rather than in chop. The freqtrade
version's ROI table and stoploss are exactly that kind of overfit number, so
they are dropped entirely — this reuses the platform's own shared P&L
trailing stop, same as every other crypto strategy, for a fair comparison.

Long and short are symmetric here (the original was long-only; every other
strategy on this platform trades both sides, so this does too).

Untested until scripts/backtest / crypto.backtest says otherwise — the prior
on any strategy pulled from a public repo is "probably no edge" until proven
on real Delta prices with real fees, same as everything else here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import adx, crossed_over, crossed_under, ema, rsi
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class RsiAdxTrendConfig:
    rsi_period: int = 10
    ema_fast: int = 5
    ema_slow: int = 10
    adx_period: int = 14
    adx_min: float = 25.0  # below this the market is chopping, not trending — HLHB's own filter
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: RsiAdxTrendConfig,
    live_price: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "rsi_adx_trend", "asset": symbol, "event": "none"}

    need = max(cfg.ema_slow, cfg.rsi_period, cfg.adx_period) + 3
    if len(candles) < need:
        ev.update(event="wait", reason=f"need {need} bars, have {len(candles)}")
        return st, ev

    close = candles["close"].astype(float)
    r = rsi(close, cfg.rsi_period)
    ef = ema(close, cfg.ema_fast)
    es = ema(close, cfg.ema_slow)
    a = adx(candles, cfg.adx_period)

    price = float(close.iloc[-1])
    ts = str(candles["datetime"].iloc[-1])
    # trailing stop/target reacts to the live mark, not just the last closed
    # candle — see crypto/strategies/cpr_trend.py for why.
    trail_price = live_price if live_price is not None else price
    adx_now = float(a.iloc[-1])
    rsi_up = crossed_over(r, 50.0)
    rsi_dn = crossed_under(r, 50.0)
    ema_up = float(ef.iloc[-2]) <= float(es.iloc[-2]) and float(ef.iloc[-1]) > float(es.iloc[-1])
    ema_dn = float(ef.iloc[-2]) >= float(es.iloc[-2]) and float(ef.iloc[-1]) < float(es.iloc[-1])
    trending = adx_now > cfg.adx_min

    pos = st["position"]
    if pos:
        side = pos["side"]
        reason = update_and_check(pos, trail_price, cfg.trail)
        if not reason:
            if side == "long" and rsi_dn and ema_dn and trending:
                reason = "RSI + EMA rolled over with ADX confirming — momentum reversed"
            elif side == "short" and rsi_up and ema_up and trending:
                reason = "RSI + EMA turned up with ADX confirming — momentum reversed"
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

    if rsi_up and ema_up and trending:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="long",
            price=price,
            reason=f"RSI crossed above 50 + EMA{cfg.ema_fast}/{cfg.ema_slow} crossed up, ADX {adx_now:.0f}",
            ts=ts,
        )
    elif rsi_dn and ema_dn and trending:
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason=f"RSI crossed below 50 + EMA{cfg.ema_fast}/{cfg.ema_slow} crossed down, ADX {adx_now:.0f}",
            ts=ts,
        )
    else:
        why = (
            "no RSI/EMA cross this bar"
            if not (rsi_up or rsi_dn)
            else f"ADX {adx_now:.0f} < {cfg.adx_min:.0f} (chop)"
        )
        ev.update(event="wait", reason=why)
    return st, ev


if __name__ == "__main__":  # self-check — no network
    # a strong downtrend (builds real ADX + drags RSI/EMA down) then a sharp
    # V-reversal — RSI crossing back above 50 and the EMA5/10 cross land on
    # the same bar while ADX (Wilder-smoothed, reacts slowly) is still > 25
    # from the prior trend, exactly the entry this strategy is built to catch
    down = [150.0 - i * 1.5 for i in range(30)]
    up = [down[-1] + 1.0] + [down[-1] + 1.0 + i * 2.0 for i in range(1, 20)]
    closes = down + up
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-08-01", periods=len(closes), freq="1h", tz="UTC"),
            "open": closes,
            "high": [c + 0.8 for c in closes],
            "low": [c - 0.8 for c in closes],
            "close": closes,
            "volume": [10.0] * len(closes),
        }
    )
    cfg = RsiAdxTrendConfig()
    state, saw_enter = None, None
    for i in range(30, len(df)):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] == "enter":
            saw_enter = ev
            break
    assert saw_enter and saw_enter["side"] == "long", saw_enter

    # an open position exits through the normal trailing stop like every other lane
    st_open = {"position": {"side": "long", "entry_price": 100.0, "entry_time": "t0"}}
    df_stop = df.copy()
    df_stop.loc[df_stop.index[-1], "close"] = 70.0  # price ran hard against the long
    _, ev2 = step("BTCUSD", df_stop, state=st_open, cfg=cfg)
    assert ev2["event"] == "exit" and "stop" in ev2["reason"], ev2

    # too few bars never crashes
    _, ev3 = step("BTCUSD", df.head(5), state=None, cfg=cfg)
    assert ev3["event"] == "wait"
    print("crypto.strategies.rsi_adx_trend self-check ok —", saw_enter["reason"])
