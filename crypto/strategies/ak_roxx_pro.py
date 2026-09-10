"""AK Roxx Pro — the "AK Roxx" (Alpha 1) confluence signal.

Faithful port, verified 2026-09-11 against the portal's own client-side signal
engine (`portal.akroxxtech.com` — `indicators.js::computeAlpha1Signals`, read
line-for-line). Full spec in `crypto/strategies/ak_roxx_pro.md`.

**Entry — long fires on the first bar all eight are true while flat** (mirror
for short); once in, no re-entry until the exit:

1. `macUp` — both AK-Channel bands rising: `SMA(high, 8)` up **and** `SMA(low, 8)`
   up vs the prior bar.
2. `close > SMA(high, 8)`.
3. `close > close[-2]`.
4. `EMA(close, 7) > EMA(close, 14)`.
5. `EMA(close, 7)` rising · 6. `EMA(close, 14)` rising.
7. `cprBuy` — no completed hourly CPR yet, **or** `close` above the hourly CPR's
   top (`max(P, TC, BC)` from the previous clock hour). Toggle `require_beyond_cpr`.
8. `pemaBull` — `EMA(hlc3, 13) > EMA(hlc3, 21) > EMA(hlc3, 34)` and all three
   rising.

**Exit — the whole exit.** Long: the first bar that **closes below the current
`SMA(low, 8)`**. Short: closes above the current `SMA(high, 8)`. No target, no
fixed stop, no P&L trail — the position rides the channel until price closes back
through the far band. (The portal's `buyActive` state machine and this exit,
verbatim.) The `close > SMA(high,8)` entry gate means you can't re-enter on the
exit bar — price has to reclaim the channel first.

Optional gate `require_alpha2_agree` (default off): the portal's separate
"Alpha 2" (15-bar break + Choppiness(14) < 38.2 + Supertrend(3, 10)) must point
the same way. Alpha 1 does not use it — this is an extra filter to A/B.

`step(symbol, candles, *, state, cfg)` — `candles` is the 5m frame (a few hundred
bars: the 34 EMA settles, the prior clock hour exists). Pure.

Earlier ports were wrong (21/34/55 ribbon; then a ratcheting stop + 1:2 target +
P&L floor the real one doesn't have) and their −$8k / −$2k backtests are void.
Re-measured after this rewrite — see RESULTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from crypto.strategies.indicators import ema, supertrend_dir
from crypto.strategies.trailing import TrailConfig


@dataclass(frozen=True)
class AkRoxxConfig:
    upper_len: int = 8               # AK Channel — SMA of highs
    lower_len: int = 8               # AK Channel — SMA of lows
    ema_short: int = 7
    ema_long: int = 14
    pema_fast: int = 13              # PEMA ribbon on hlc3
    pema_mid: int = 21
    pema_slow: int = 34
    slope_lookback: int = 1          # bars back for every "rising / falling" check
    timeframe: str = "1h"            # the channel-break exit is a proper swing stop
                                     #  on 1h (8h of lows); on 5m it's a 40-min leash
                                     #  and gets chopped out — 2026-09-11 backtest.
    require_beyond_cpr: bool = True  # condition 7 — outside the previous 1H CPR
    require_alpha2_agree: bool = False  # extra filter: Alpha 2 must point the same way
    a2_chan_len: int = 15
    a2_chop_max: float = 38.2
    a2_st_period: int = 10
    a2_st_mult: float = 3.0
    trail: TrailConfig = field(default_factory=TrailConfig)  # lane bracket-SL only; exit is the channel


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _rising(s: pd.Series, lb: int) -> bool:
    return float(s.iloc[-1]) > float(s.iloc[-1 - lb])


def _falling(s: pd.Series, lb: int) -> bool:
    return float(s.iloc[-1]) < float(s.iloc[-1 - lb])


def _hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0


def _sma(c5: pd.DataFrame, length: int, which: str) -> pd.Series:
    return c5[which].astype(float).rolling(length).mean()


def _hourly_cpr(c5: pd.DataFrame) -> tuple[float, float] | None:
    """(cpr_min, cpr_max) from the previous clock hour, or None if that hour
    isn't fully covered by the frame. `P=(H+L+C)/3`, `BC=(H+L)/2`, `TC=2P-BC`."""
    dt = pd.to_datetime(c5["datetime"])
    cur_hour = dt.iloc[-1].floor("1h")
    prev_hour = cur_hour - pd.Timedelta(hours=1)
    seg = c5.loc[(dt >= prev_hour) & (dt < cur_hour)]
    if seg.empty or dt.iloc[0] > prev_hour:
        return None
    hi, lo, cl = float(seg["high"].max()), float(seg["low"].min()), float(seg["close"].iloc[-1])
    p = (hi + lo + cl) / 3.0
    bc = (hi + lo) / 2.0
    tc = 2.0 * p - bc
    return min(p, bc, tc), max(p, bc, tc)


def _choppiness(df: pd.DataFrame, length: int) -> float:
    h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
    atr_sum = tr.rolling(length).sum().iloc[-1]
    rng = h.rolling(length).max().iloc[-1] - low.rolling(length).min().iloc[-1]
    if not rng or atr_sum <= 0:
        return 100.0
    return float(100.0 * np.log10(atr_sum / rng) / np.log10(length))


def _alpha1_dir(c5: pd.DataFrame, cfg: AkRoxxConfig, cpr: tuple[float, float] | None) -> int:
    """+1 long / -1 short / 0 — the 8-condition `rawBuy` / `rawSell`, on the last bar."""
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    lb = cfg.slope_lookback

    up_ch = _sma(c5, cfg.upper_len, "high")
    lo_ch = _sma(c5, cfg.lower_len, "low")
    if pd.isna(up_ch.iloc[-1 - lb]) or pd.isna(lo_ch.iloc[-1 - lb]):
        return 0
    mac_up = _rising(up_ch, lb) and _rising(lo_ch, lb)
    mac_dn = _falling(up_ch, lb) and _falling(lo_ch, lb)

    e_s, e_l = ema(close, cfg.ema_short), ema(close, cfg.ema_long)
    hlc3 = _hlc3(c5)
    pf, pm, ps = ema(hlc3, cfg.pema_fast), ema(hlc3, cfg.pema_mid), ema(hlc3, cfg.pema_slow)
    pfn, pmn, psn = float(pf.iloc[-1]), float(pm.iloc[-1]), float(ps.iloc[-1])
    pema_bull = pfn > pmn > psn and _rising(pf, lb) and _rising(pm, lb) and _rising(ps, lb)
    pema_bear = pfn < pmn < psn and _falling(pf, lb) and _falling(pm, lb) and _falling(ps, lb)

    prev_close = float(close.iloc[-2])
    cpr_buy = (not cfg.require_beyond_cpr) or cpr is None or price > cpr[1]
    cpr_sell = (not cfg.require_beyond_cpr) or cpr is None or price < cpr[0]

    long_ok = (
        mac_up and price > float(up_ch.iloc[-1]) and price > prev_close
        and float(e_s.iloc[-1]) > float(e_l.iloc[-1]) and _rising(e_s, lb) and _rising(e_l, lb)
        and cpr_buy and pema_bull
    )
    short_ok = (
        mac_dn and price < float(lo_ch.iloc[-1]) and price < prev_close
        and float(e_s.iloc[-1]) < float(e_l.iloc[-1]) and _falling(e_s, lb) and _falling(e_l, lb)
        and cpr_sell and pema_bear
    )
    return 1 if long_ok else -1 if short_ok else 0


def _alpha2_dir(c5: pd.DataFrame, cfg: AkRoxxConfig, cpr: tuple[float, float] | None) -> int:
    close = c5["close"].astype(float)
    lb = cfg.slope_lookback
    if len(c5) < cfg.a2_chan_len + lb + 3:
        return 0
    up_ch = _sma(c5, cfg.a2_chan_len, "high")
    lo_ch = _sma(c5, cfg.a2_chan_len, "low")
    e_s, e_l = ema(close, cfg.ema_short), ema(close, cfg.ema_long)

    def _brk(i: int, direction: int) -> bool:
        px, pxp = float(close.iloc[i]), float(close.iloc[i - 1])
        if direction == 1:
            return (
                float(up_ch.iloc[i]) > float(up_ch.iloc[i - lb])
                and float(lo_ch.iloc[i]) > float(lo_ch.iloc[i - lb])
                and px > float(up_ch.iloc[i]) and px > pxp
                and float(e_s.iloc[i]) > float(e_l.iloc[i])
                and float(e_s.iloc[i]) > float(e_s.iloc[i - lb])
                and float(e_l.iloc[i]) > float(e_l.iloc[i - lb])
            )
        return (
            float(up_ch.iloc[i]) < float(up_ch.iloc[i - lb])
            and float(lo_ch.iloc[i]) < float(lo_ch.iloc[i - lb])
            and px < float(lo_ch.iloc[i]) and px < pxp
            and float(e_s.iloc[i]) < float(e_l.iloc[i])
            and float(e_s.iloc[i]) < float(e_s.iloc[i - lb])
            and float(e_l.iloc[i]) < float(e_l.iloc[i - lb])
        )

    price = float(close.iloc[-1])
    if _choppiness(c5, 14) >= cfg.a2_chop_max:
        return 0
    st = supertrend_dir(c5, cfg.a2_st_period, cfg.a2_st_mult)
    if _brk(len(c5) - 1, 1) and not _brk(len(c5) - 2, 1) and st == 1 and (cpr is None or price > cpr[1]):
        return 1
    if _brk(len(c5) - 1, -1) and not _brk(len(c5) - 2, -1) and st == -1 and (cpr is None or price < cpr[0]):
        return -1
    return 0


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: AkRoxxConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ak_roxx_pro", "asset": symbol, "event": "none"}

    need = max(cfg.pema_slow, cfg.upper_len, cfg.a2_chan_len) + cfg.slope_lookback + 3
    if len(candles) < max(need, 40):
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    pos = st["position"]

    # ---- exit: close back through the far channel band ----
    if pos:
        long = pos["side"] == "long"
        band = float(_sma(c5, cfg.lower_len if long else cfg.upper_len,
                          "low" if long else "high").iloc[-1])
        pos["chan_stop"] = band  # for display / the lane
        if (long and price < band) or (not long and price > band):
            st["position"] = None
            ev.update(event="exit", side=pos["side"], price=price, ts=ts,
                      reason=f"close {price:.2f} back through SMA{cfg.lower_len if long else cfg.upper_len} "
                             f"{'low' if long else 'high'} band {band:.2f}")
        else:
            ev.update(event="hold", side=pos["side"], price=price)
        return st, ev

    # ---- entry: first flat bar the confluence is true ----
    cpr = _hourly_cpr(c5)
    if cfg.require_beyond_cpr and cpr is None:
        ev.update(event="wait", reason="no prior 1H CPR yet")
        return st, ev

    d = _alpha1_dir(c5, cfg, cpr)
    if d == 0:
        ev.update(event="wait", reason="Alpha 1 confluence not met")
        return st, ev
    if cfg.require_alpha2_agree and _alpha2_dir(c5, cfg, cpr) != d:
        ev.update(event="wait", reason="Alpha 2 does not agree")
        return st, ev

    want = "long" if d == 1 else "short"
    band = float(_sma(c5, cfg.lower_len if d == 1 else cfg.upper_len,
                      "low" if d == 1 else "high").iloc[-1])
    st["position"] = {
        "side": want, "entry_price": price, "entry_time": ts, "chan_stop": band,
    }
    ev.update(
        event="enter", side=want, price=price, ts=ts,
        reason=(
            f"AK Roxx: 8/8 channel + 7/14 EMA + PEMA {cfg.pema_fast}/{cfg.pema_mid}/{cfg.pema_slow} "
            f"stacked {'up' if d == 1 else 'down'}"
            + (", beyond 1H CPR" if cfg.require_beyond_cpr else "")
            + (", Alpha 2 agrees" if cfg.require_alpha2_agree else "")
        ),
    )
    return st, ev


def _demo_frame() -> pd.DataFrame:
    """Base chop (sets the CPR) -> accelerating rally (fires the long) -> a drop
    that closes below SMA(low,8) (fires the exit)."""
    seg = [
        100.0 + np.sin(np.linspace(0, 6, 300)) * 1.5,
        100.0 + np.linspace(0, 60, 240) ** 1.15,     # rally
        100.0 + np.linspace(60, 60, 3) ** 1.15,      # tiny plateau
    ]
    px = np.concatenate(seg)
    px = np.concatenate([px, [px[-1] - 40, px[-1] - 55, px[-1] - 55]])  # drop through the low band
    idx = pd.date_range("2026-09-06 00:00", periods=len(px), freq="5min", tz="UTC")
    return pd.DataFrame(
        {"datetime": idx, "open": px, "high": px + 0.1, "low": px - 0.1, "close": px,
         "volume": [10.0] * len(px)}
    )


if __name__ == "__main__":  # self-check — enter on the rally, exit on the close through the low band
    df = _demo_frame()
    cfg = AkRoxxConfig()
    state, entered, exited = None, None, None
    for i in range(320, len(df)):
        state, evt = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if evt["event"] == "enter":
            entered = evt
        elif evt["event"] == "exit":
            exited = evt
            break
    assert entered and entered["side"] == "long", entered
    assert exited and "band" in exited["reason"], exited
    flat = df.assign(open=100.0, high=100.3, low=99.7, close=100.0)
    _, ev2 = step("BTCUSD", flat, state=None, cfg=cfg)
    assert ev2["event"] == "wait", ev2
    print("crypto.strategies.ak_roxx_pro self-check ok — enter then", exited["reason"][:40])
