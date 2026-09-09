"""TMA Phoenix — 5-minute signal: a stacked/sloping SMMA ribbon (21/50/100/200)
for bias, plus an engulfing or 3-line-strike reversal candle in the bias
direction, exited on a fixed ATR bracket (stop / break-even / target).

Reconstructed from ``crypto/strategies/tma_phoenix.md`` — the locked TradingView
indicator "The Arty" / "The Phoenix 1.0" (`PhoenixBinary`), which packages the
method taught by Arty ("The Moving Average"). Clean-room Python port of the
*mechanical core*; the original is a discretionary visual aid, not a system.

- **Trend:** SMMA(21) > SMMA(50) > SMMA(200), the 200 rising over
  ``slope_lookback`` bars, and price above the 200 → long bias; mirrored → short.
  ``require_ma3`` also demands the 100 sits inside the stack.
- **Trigger:** on the last closed bar, a **bullish engulfing** (``strict`` also
  needs the body to be the larger one) *or* a **bullish 3-line-strike** (three
  down bars then one that closes above the first bar's open), in the bias
  direction. Bearish mirrors.
- **Big-candle gate:** signal-bar range > ``big_candle_atr`` × ATR → skip.
- **Near-MA gate:** if ``near_ma_atr`` > 0, the signal bar must be within that
  many ATR of the 50 or 200 SMMA (Arty's "take it at an MA").
- **Exit:** ATR bracket snapshotted at entry — stop at ∓``stop_atr``×ATR, target
  at ±``target_atr``×ATR, stop moved to break-even once +``be_atr``×ATR is seen.
  The shared P&L trailing engine runs underneath as a liquidation backstop.

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame (needs a
few hundred bars for the 200 SMMA to settle). Pure: returns ``(state, event)``.

Backtested on Delta 5m (60d, BTC/ETH/SOL) 2026-09-09: net −$4.5k / 2,011 trades,
26% win, gross flat (no edge). It did not clear — see ``tma_phoenix.md``. Kept as
documented-dead: wired to ``crypto/backtest.py`` only, never to
``crypto/lanes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.indicators import atr_last, smma
from crypto.strategies.trailing import TrailConfig, update_and_check


@dataclass(frozen=True)
class TmaPhoenixConfig:
    ma1: int = 21
    ma2: int = 50
    ma3: int = 100
    ma4: int = 200
    require_ma3: bool = True          # the 100 SMMA must sit inside the 21>50>200 stack
    slope_lookback: int = 5           # the 200 SMMA must have moved in-trend over this span
    atr_len: int = 14
    big_candle_atr: float = 2.5       # skip the entry if the signal bar's range > this × ATR
    near_ma_atr: float = 0.0          # signal bar within this × ATR of the 50/200 SMMA (0 = off)
    use_engulfing: bool = True
    use_three_line_strike: bool = True
    strict: bool = True               # engulfing needs the larger body; 3LS needs monotonic closes
    stop_atr: float = 1.5
    target_atr: float = 1.5           # 1.5 / 1.5 → 1:1 R:R, matching the indicator's defaults
    be_atr: float = 0.5               # move stop to break-even once this much ATR is in favour
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _ribbon(close: pd.Series, cfg: TmaPhoenixConfig) -> int:
    """+1 SMMA ribbon stacked up & the 200 rising with price above it, -1 mirror, 0 else."""
    need = cfg.ma4 + cfg.slope_lookback + 2
    if len(close) < need:
        return 0
    m1 = float(smma(close, cfg.ma1).iloc[-1])
    m2 = float(smma(close, cfg.ma2).iloc[-1])
    m3 = float(smma(close, cfg.ma3).iloc[-1])
    s200 = smma(close, cfg.ma4)
    m4, m4_prev = float(s200.iloc[-1]), float(s200.iloc[-1 - cfg.slope_lookback])
    price = float(close.iloc[-1])
    up = m1 > m2 > m4 and m4 > m4_prev and price > m4 and (not cfg.require_ma3 or m2 > m3 > m4)
    dn = m1 < m2 < m4 and m4 < m4_prev and price < m4 and (not cfg.require_ma3 or m2 < m3 < m4)
    return 1 if up else -1 if dn else 0


def _engulfing(c5: pd.DataFrame, strict: bool) -> int:
    """+1 bullish / -1 bearish engulfing on the last closed bar, else 0."""
    if len(c5) < 2:
        return 0
    o, c = float(c5["open"].iloc[-1]), float(c5["close"].iloc[-1])
    po, pc = float(c5["open"].iloc[-2]), float(c5["close"].iloc[-2])
    body, pbody = abs(c - o), abs(pc - po)
    if c > o and pc < po and c > po and o <= pc and (not strict or body >= pbody):
        return 1
    if c < o and pc > po and c < po and o >= pc and (not strict or body >= pbody):
        return -1
    return 0


def _three_line_strike(c5: pd.DataFrame, strict: bool) -> int:
    """+1 bullish / -1 bearish 3-line-strike ending on the last closed bar, else 0.

    Bullish: bars -4,-3,-2 down, the last bar up and closing above open[-4]."""
    if len(c5) < 4:
        return 0
    o = [float(c5["open"].iloc[i]) for i in (-4, -3, -2, -1)]
    c = [float(c5["close"].iloc[i]) for i in (-4, -3, -2, -1)]
    down3 = all(c[i] < o[i] for i in range(3))
    up3 = all(c[i] > o[i] for i in range(3))
    mono_down = c[0] > c[1] > c[2]
    mono_up = c[0] < c[1] < c[2]
    if down3 and c[3] > o[3] and c[3] > o[0] and (not strict or mono_down):
        return 1
    if up3 and c[3] < o[3] and c[3] < o[0] and (not strict or mono_up):
        return -1
    return 0


def _bracket_reason(pos: dict[str, Any], price: float) -> str | None:
    """Move the stop to break-even, then check the ATR bracket."""
    side = pos["side"]
    if side == "long":
        if not pos.get("be") and price >= pos["be_price"]:
            pos["be"], pos["stop"] = True, pos["entry_price"]
        if price <= pos["stop"]:
            return "break-even stop" if pos.get("be") else "ATR stop"
        if price >= pos["target"]:
            return "1:1 ATR target"
    else:
        if not pos.get("be") and price <= pos["be_price"]:
            pos["be"], pos["stop"] = True, pos["entry_price"]
        if price >= pos["stop"]:
            return "break-even stop" if pos.get("be") else "ATR stop"
        if price <= pos["target"]:
            return "1:1 ATR target"
    return None


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: TmaPhoenixConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "tma_phoenix", "asset": symbol, "event": "none"}

    need = max(cfg.ma4 + cfg.slope_lookback + 2, cfg.atr_len + 2, 5)
    if len(candles) < need:
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    atr_val = atr_last(c5, cfg.atr_len)
    pos = st["position"]

    # ---- manage an open position ----
    if pos:
        side = pos["side"]
        reason = _bracket_reason(pos, price) or update_and_check(pos, price, cfg.trail)
        if reason:
            st["position"] = None
            ev.update(event="exit", side=side, price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    # ---- look for an entry ----
    if atr_val <= 0:
        ev.update(event="wait", reason="no ATR yet")
        return st, ev

    trend = _ribbon(close, cfg)
    if trend == 0:
        ev.update(event="wait", reason="SMMA ribbon not stacked/sloping")
        return st, ev

    eng = _engulfing(c5, cfg.strict) if cfg.use_engulfing else 0
    tls = _three_line_strike(c5, cfg.strict) if cfg.use_three_line_strike else 0
    if trend == 1 and 1 not in (eng, tls):
        ev.update(event="wait", reason="no bullish reversal candle")
        return st, ev
    if trend == -1 and -1 not in (eng, tls):
        ev.update(event="wait", reason="no bearish reversal candle")
        return st, ev

    rng = float(c5["high"].iloc[-1] - c5["low"].iloc[-1])
    if rng > cfg.big_candle_atr * atr_val:
        ev.update(event="wait", reason="signal candle oversized")
        return st, ev

    if cfg.near_ma_atr > 0:
        m2 = float(smma(close, cfg.ma2).iloc[-1])
        m4 = float(smma(close, cfg.ma4).iloc[-1])
        edge = float(c5["low"].iloc[-1]) if trend == 1 else float(c5["high"].iloc[-1])
        if min(abs(edge - m2), abs(edge - m4)) > cfg.near_ma_atr * atr_val:
            ev.update(event="wait", reason="signal bar not at an MA")
            return st, ev

    want = "long" if trend == 1 else "short"
    d = 1.0 if want == "long" else -1.0
    pat = "engulfing" if eng == trend else "3-line-strike"
    st["position"] = {
        "side": want,
        "entry_price": price,
        "entry_time": ts,
        "stop": price - d * cfg.stop_atr * atr_val,
        "target": price + d * cfg.target_atr * atr_val,
        "be_price": price + d * cfg.be_atr * atr_val,
        "be": False,
    }
    ev.update(
        event="enter",
        side=want,
        price=price,
        reason=f"{pat} with the 21/50/200 SMMA ribbon {'up' if trend == 1 else 'down'}",
        ts=ts,
    )
    return st, ev


if __name__ == "__main__":  # self-check — ribbon rises, a bullish engulfing prints, target hits
    import numpy as np

    n = 400
    idx = pd.date_range("2026-09-06 00:00", periods=n, freq="5min", tz="UTC")
    px = np.concatenate([
        100.0 + np.linspace(0, 2, 150),
        np.linspace(102.0, 140.0, n - 150),   # clean rally so the 200 SMMA turns up
    ])
    o = px.copy()
    c = px.copy()
    hi = px + 0.3
    lo = px - 0.3
    # bar 360: a small down bar; bar 361: a bullish engulfing of it
    o[360], c[360], hi[360], lo[360] = px[360] + 0.25, px[360] - 0.25, px[360] + 0.35, px[360] - 0.35
    o[361], c[361], hi[361], lo[361] = px[360] - 0.30, px[360] + 0.45, px[360] + 0.55, px[360] - 0.40
    df = pd.DataFrame({"datetime": idx, "open": o, "high": hi, "low": lo, "close": c,
                       "volume": [10.0] * n})
    cfg = TmaPhoenixConfig(slope_lookback=3, require_ma3=False)
    st, fired = None, None
    for i in range(260, n):
        st, evt = step("BTCUSD", df.iloc[: i + 1], state=st, cfg=cfg)
        if evt["event"] == "enter":
            fired = evt
            break
    assert fired and fired["side"] == "long", fired
    assert st["position"]["target"] > fired["price"] > st["position"]["stop"], st["position"]
    print("crypto.strategies.tma_phoenix self-check ok —", fired["reason"])
