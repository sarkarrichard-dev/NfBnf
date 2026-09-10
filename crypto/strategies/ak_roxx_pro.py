"""AK Roxx Pro — the "AK Roxx" (Alpha 1) 5-minute confluence signal.

Rebuilt 2026-09-10 from the live portal (``portal.akroxxtech.com``) — its
client-side JS recomputes the locked TradingView signal and the author's own
comments say it is ported verbatim from the indicator's math. Full spec +
parameter table in ``crypto/strategies/ak_roxx_pro.md``.

**Alpha 1 long entry — all eight, on a closed 5m bar** (mirror for short):

1. ``macUp`` — both AK-Channel bands rising: ``SMA(high, 8)`` up **and**
   ``SMA(low, 8)`` up vs the prior bar.
2. ``close > SMA(high, 8)`` (the upper channel band).
3. ``close > close[-2]`` (above the previous bar's close).
4. ``EMA(close, 7) > EMA(close, 14)``.
5. ``EMA(close, 7)`` rising · 6. ``EMA(close, 14)`` rising.
7. **1H CPR gate** — no completed hourly CPR yet, *or* ``close`` above the
   hourly CPR's top (``max(P, TC, BC)``). ``P=(H+L+C)/3``, ``BC=(H+L)/2``,
   ``TC=2P-BC`` from the **previous** clock hour. Inside the range is the
   indicator's "NO TRADE ZONE". Toggle: ``require_beyond_cpr``.
8. **PEMA ribbon** — ``EMA(hlc3, 13) > EMA(hlc3, 21) > EMA(hlc3, 34)`` and all
   three rising (stacked **and** sloping).

Optional 9th gate (``require_alpha2_agree``): the portal's "Alpha 2 / 5m Trend
Strategy" must point the same way — a *fresh* break of the ``SMA(15)`` channel
edge with the 7/14 EMAs aligned, ``Choppiness(14) < 38.2``, price beyond the
hourly CPR, and ``Supertrend(3.0, 10)`` agreeing. "Both engines agree" is the
strongest read the portal gives.

**Trend-ride**: once in, no new signal until the exit. The exit is the portal's
own — a channel-edge price stop (``SMA(low, 8)`` for a long, ``SMA(high, 8)`` for
a short) that ratchets toward price, plus a fixed 1:``rr`` target and a hard P&L
floor for a gap. ``use_channel_stop=False`` swaps in the lane's shared
P&L-percent trail (an A/B knob — the shared trail's 0.1%-price stop at 100×
turns this into scalp-churn, see ``RESULTS.md``).

``step(symbol, candles, *, state, cfg)`` — ``candles`` is the 5m frame (a few
hundred bars: the 34 EMA settles, the prior clock hour exists). Pure.

Not yet forward-measured on the real logic. The old −$8k backtest was on a wrong
reconstruction (21/34/55 ribbon) and is void. Watch-forward lane, not a proven
edge — every 5m crypto config here has been net-negative after Delta costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from crypto.strategies.indicators import atr_last, ema, supertrend_dir
from crypto.strategies.trailing import TrailConfig, pnl_pct, update_and_check


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
    require_beyond_cpr: bool = True  # condition 7 — outside the previous 1H CPR
    require_alpha2_agree: bool = False  # also require the Alpha 2 direction to match
    a2_chan_len: int = 15            # Alpha 2 SMA channel
    a2_chop_max: float = 38.2        # Alpha 2 — Choppiness(14) must be below this
    a2_st_period: int = 10           # Alpha 2 Supertrend
    a2_st_mult: float = 3.0
    atr_len: int = 14
    big_candle_atr: float = 0.0      # >0: skip when the signal bar's range exceeds this × ATR
    rr: float = 2.0                  # fixed target = rr × initial stop distance (1:2)
    # exit — the portal's own: a ratcheting channel-edge price stop + a fixed
    # 1:rr target. Set use_channel_stop=False to fall back to the lane's shared
    # P&L-percent trail instead (an A/B knob for the optimiser).
    use_channel_stop: bool = True
    hard_stop_pnl_pct: float = 60.0  # disaster floor when the channel stop gaps through
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def _rising(s: pd.Series, lb: int) -> bool:
    return float(s.iloc[-1]) > float(s.iloc[-1 - lb])


def _falling(s: pd.Series, lb: int) -> bool:
    return float(s.iloc[-1]) < float(s.iloc[-1 - lb])


def _hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0


def _hourly_cpr(c5: pd.DataFrame) -> tuple[float, float] | None:
    """(cpr_min, cpr_max) from the previous clock hour, or None if that hour
    isn't fully covered by the frame."""
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
    """Choppiness Index of the last bar. 100·log10(ΣATR1 / range) / log10(n)."""
    h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
    atr_sum = tr.rolling(length).sum().iloc[-1]
    rng = h.rolling(length).max().iloc[-1] - low.rolling(length).min().iloc[-1]
    if not rng or atr_sum <= 0:
        return 100.0
    return float(100.0 * np.log10(atr_sum / rng) / np.log10(length))


def _alpha1_dir(c5: pd.DataFrame, cfg: AkRoxxConfig, cpr: tuple[float, float] | None) -> int:
    close = c5["close"].astype(float)
    price = float(close.iloc[-1])
    lb = cfg.slope_lookback

    up_ch = c5["high"].astype(float).rolling(cfg.upper_len).mean()
    lo_ch = c5["low"].astype(float).rolling(cfg.lower_len).mean()
    if pd.isna(up_ch.iloc[-1 - lb]) or pd.isna(lo_ch.iloc[-1 - lb]):
        return 0
    mac_up = _rising(up_ch, lb) and _rising(lo_ch, lb)
    mac_dn = _falling(up_ch, lb) and _falling(lo_ch, lb)

    e_s, e_l = ema(close, cfg.ema_short), ema(close, cfg.ema_long)
    hlc3 = _hlc3(c5)
    pf, pm, ps = (ema(hlc3, cfg.pema_fast), ema(hlc3, cfg.pema_mid), ema(hlc3, cfg.pema_slow))
    pfn, pmn, psn = float(pf.iloc[-1]), float(pm.iloc[-1]), float(ps.iloc[-1])
    pema_bull = pfn > pmn > psn and _rising(pf, lb) and _rising(pm, lb) and _rising(ps, lb)
    pema_bear = pfn < pmn < psn and _falling(pf, lb) and _falling(pm, lb) and _falling(ps, lb)

    prev_close = float(close.iloc[-2])
    cpr_ok_long = (not cfg.require_beyond_cpr) or cpr is None or price > cpr[1]
    cpr_ok_short = (not cfg.require_beyond_cpr) or cpr is None or price < cpr[0]

    long_ok = (
        mac_up
        and price > float(up_ch.iloc[-1])
        and price > prev_close
        and float(e_s.iloc[-1]) > float(e_l.iloc[-1])
        and _rising(e_s, lb) and _rising(e_l, lb)
        and cpr_ok_long
        and pema_bull
    )
    short_ok = (
        mac_dn
        and price < float(lo_ch.iloc[-1])
        and price < prev_close
        and float(e_s.iloc[-1]) < float(e_l.iloc[-1])
        and _falling(e_s, lb) and _falling(e_l, lb)
        and cpr_ok_short
        and pema_bear
    )
    return 1 if long_ok else -1 if short_ok else 0


def _alpha2_dir(c5: pd.DataFrame, cfg: AkRoxxConfig, cpr: tuple[float, float] | None) -> int:
    """Alpha 2 ("5m Trend Strategy") direction: a fresh SMA(15)-channel break
    with the 7/14 EMAs aligned, low choppiness, beyond CPR, Supertrend agreeing."""
    close = c5["close"].astype(float)
    lb = cfg.slope_lookback
    if len(c5) < cfg.a2_chan_len + lb + 3:
        return 0
    up_ch = c5["high"].astype(float).rolling(cfg.a2_chan_len).mean()
    lo_ch = c5["low"].astype(float).rolling(cfg.a2_chan_len).mean()
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
    chop = _choppiness(c5, 14)
    if chop >= cfg.a2_chop_max:
        return 0
    st = supertrend_dir(c5, cfg.a2_st_period, cfg.a2_st_mult)
    if _brk(len(c5) - 1, 1) and not _brk(len(c5) - 2, 1) and st == 1:
        if cpr is None or price > cpr[1]:
            return 1
    if _brk(len(c5) - 1, -1) and not _brk(len(c5) - 2, -1) and st == -1:
        if cpr is None or price < cpr[0]:
            return -1
    return 0


def _band(c5: pd.DataFrame, length: int, which: str) -> float:
    """Last value of the AK-Channel SMA — ``high`` band or ``low`` band."""
    return float(c5[which].astype(float).rolling(length).mean().iloc[-1])


def _manage_channel(pos: dict[str, Any], c5: pd.DataFrame, price: float, cfg: AkRoxxConfig) -> str | None:
    """The portal's own exit: a channel-edge stop that ratchets toward price,
    a fixed 1:rr target, and a hard P&L floor for a gap through the stop."""
    long = pos["side"] == "long"
    band = _band(c5, cfg.lower_len if long else cfg.upper_len, "low" if long else "high")
    stop = pos["chan_stop"] = (
        max(pos["chan_stop"], band) if long else min(pos["chan_stop"], band)
    )
    hard = pnl_pct(float(pos["entry_price"]), price, pos["side"], cfg.trail.leverage)
    if hard <= -cfg.hard_stop_pnl_pct:
        return f"hard stop {hard:+.0f}% P&L"
    if (long and price <= stop) or (not long and price >= stop):
        return f"channel stop {stop:.2f}"
    tgt = pos["target"]
    if (long and price >= tgt) or (not long and price <= tgt):
        return f"1:{cfg.rr:g} target {tgt:.2f}"
    return None


def _target_hit(pos: dict[str, Any], price: float, cfg: AkRoxxConfig) -> bool:
    cur = pnl_pct(float(pos["entry_price"]), price, pos["side"], cfg.trail.leverage)
    return cur >= cfg.rr * cfg.trail.stop_pnl_pct


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: AkRoxxConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ak_roxx_pro", "asset": symbol, "event": "none"}

    need = max(
        cfg.pema_slow + cfg.slope_lookback + 2,
        cfg.upper_len + cfg.slope_lookback + 2,
        cfg.a2_chan_len + cfg.slope_lookback + 3,
        cfg.atr_len + 2,
        30,
    )
    if len(candles) < need:
        ev.update(event="wait", reason="not enough 5m history")
        return st, ev

    c5 = candles.reset_index(drop=True)
    price = float(c5["close"].astype(float).iloc[-1])
    ts = str(c5["datetime"].iloc[-1])
    pos = st["position"]

    if pos:
        if cfg.use_channel_stop and "chan_stop" in pos:
            reason = _manage_channel(pos, c5, price, cfg)
        else:
            reason = update_and_check(pos, price, cfg.trail)
            if not reason and _target_hit(pos, price, cfg):
                reason = f"1:{cfg.rr:g} target"
        if reason:
            st["position"] = None
            ev.update(event="exit", side=pos["side"], price=price, reason=reason, ts=ts)
        else:
            ev.update(event="hold", side=pos["side"], price=price)
        return st, ev

    cpr = _hourly_cpr(c5)
    if cfg.require_beyond_cpr and cpr is None:
        ev.update(event="wait", reason="no prior 1H CPR yet")
        return st, ev

    d = _alpha1_dir(c5, cfg, cpr)
    if d == 0:
        ev.update(event="wait", reason="Alpha 1 confluence not met")
        return st, ev

    # The portal's signal is a discrete event on the bar the confluence FLIPS on,
    # not a state you can re-enter every bar it stays true. Require a fresh
    # transition: the prior bar was not already in this same direction.
    if len(c5) > 1 and _alpha1_dir(c5.iloc[:-1].reset_index(drop=True), cfg, cpr) == d:
        ev.update(event="wait", reason="Alpha 1 already in confluence — no fresh signal")
        return st, ev

    if cfg.require_alpha2_agree and _alpha2_dir(c5, cfg, cpr) != d:
        ev.update(event="wait", reason="Alpha 2 does not agree")
        return st, ev

    if cfg.big_candle_atr > 0:
        atr_val = atr_last(c5, cfg.atr_len)
        rng = float(c5["high"].iloc[-1] - c5["low"].iloc[-1])
        if atr_val > 0 and rng > cfg.big_candle_atr * atr_val:
            ev.update(event="wait", reason="signal candle oversized — waiting for a retrace")
            return st, ev

    want = "long" if d == 1 else "short"
    pos_new: dict[str, Any] = {"side": want, "entry_price": price, "entry_time": ts}
    if cfg.use_channel_stop:
        init_stop = _band(c5, cfg.lower_len if d == 1 else cfg.upper_len,
                          "low" if d == 1 else "high")
        risk = abs(price - init_stop) or price * 0.001
        pos_new["chan_stop"] = init_stop
        pos_new["target"] = price + cfg.rr * risk * (1 if d == 1 else -1)
    st["position"] = pos_new
    ev.update(
        event="enter",
        side=want,
        price=price,
        reason=(
            f"AK Roxx: 8/8 channel + 7/14 EMA + PEMA {cfg.pema_fast}/{cfg.pema_mid}/{cfg.pema_slow} "
            f"stacked {'up' if d == 1 else 'down'}"
            + (", beyond 1H CPR" if cfg.require_beyond_cpr else "")
            + (", Alpha 2 agrees" if cfg.require_alpha2_agree else "")
        ),
        ts=ts,
    )
    return st, ev


def _demo_frame() -> pd.DataFrame:
    """Base chop (sets the CPR) → rally → pullback (breaks the confluence) →
    resumed rally — so the signal fires *fresh* on the resume, not every bar."""
    seg = [
        100.0 + np.sin(np.linspace(0, 6, 360)) * 1.5,
        100.0 + np.linspace(0, 40, 180) ** 1.15,          # leg 1
        140.0 - np.linspace(0, 12, 60),                   # pullback
        128.0 + np.linspace(0, 55, 300) ** 1.15,          # leg 2 — the fresh signal
    ]
    px = np.concatenate(seg)
    idx = pd.date_range("2026-09-06 00:00", periods=len(px), freq="5min", tz="UTC")
    return pd.DataFrame(
        {"datetime": idx, "open": px, "high": px + 0.1, "low": px - 0.1, "close": px,
         "volume": [10.0] * len(px)}
    )


if __name__ == "__main__":  # self-check
    df = _demo_frame()
    cfg = AkRoxxConfig()
    fired, state = None, None
    for i in range(560, len(df)):
        state, evt = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if evt["event"] == "enter":
            fired = evt
            break
    assert fired and fired["side"] == "long", fired
    flat = df.assign(open=100.0, high=100.3, low=99.7, close=100.0)
    _, ev2 = step("BTCUSD", flat, state=None, cfg=cfg)
    assert ev2["event"] == "wait", ev2
    print("crypto.strategies.ak_roxx_pro self-check ok —", fired["reason"])
