"""
Spot-replay backtest for the directional index-futures strategy.

Index-futures track spot closely (small, decaying basis), so replaying the
strategy on cached spot candles is a fair approximation of the futures P&L —
unlike the options proxy, there is no premium/theta guesswork here. Costs use
the real index-futures charge schedule.

fills:
  * entries and 15m-flip exits    -> next 5m bar open (no look-ahead)
  * hard stop / trailing stop hit  -> at the stop level, intrabar, minus slippage
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.charges import futures_round_trip_rupees, futures_slippage_rupees
from index_ai.strategies.futures.config import FuturesConfig, config_for
from index_ai.strategies.futures.engine import FLAT, LONG, trend_series

_15M = pd.Timedelta("15min")


def _day(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["datetime"]).dt.date


def replay_futures_session(
    bars5_today: pd.DataFrame,
    bars15_today: pd.DataFrame,
    bars15_prev: pd.DataFrame,
    cfg: FuturesConfig,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    day_loss_pts = 0.0
    n_entries = 0
    t5 = pd.to_datetime(bars5_today["datetime"])

    # opening range (for entry_mode="orb")
    open_ts = t5.iloc[0] if len(t5) else None
    orb_mask = (
        (t5 <= open_ts + pd.Timedelta(minutes=cfg.orb_minutes)) if open_ts is not None else t5 == t5
    )
    orb_hi = float(bars5_today.loc[orb_mask.to_numpy(), "high"].max()) if orb_mask.any() else 0.0
    orb_lo = float(bars5_today.loc[orb_mask.to_numpy(), "low"].min()) if orb_mask.any() else 0.0
    orb_range_pct = (orb_hi - orb_lo) / max(orb_lo, 1.0) * 100.0 if orb_hi > 0 else 0.0
    day_range_ok = orb_range_pct >= cfg.min_orb_range_pct

    # optional 5m Supertrend confirmation
    st5_dir = None
    if cfg.require_5m_st_aligned and len(bars5_today) >= cfg.st5_period + 2:
        from index_ai.strategies.supertrend import compute_supertrend

        st5_dir = compute_supertrend(
            bars5_today, period=cfg.st5_period, multiplier=cfg.st5_mult
        )["supertrend_direction"].to_numpy()

    # 15m trend, computed once: carry indicators across yesterday->today, then
    # read the direction of the last fully-closed 15m bar for each 5m timestamp.
    full15 = pd.concat([bars15_prev, bars15_today], ignore_index=True)
    if len(full15) < cfg.trend_min_bars:
        return trades
    t15 = pd.to_datetime(full15["datetime"]).to_numpy()
    dir15 = trend_series(full15, bars15_prev, cfg).to_numpy()
    n_prev = len(bars15_prev)

    def _dir_at(ts: pd.Timestamp) -> int:
        closed = (t15 + _15M.to_timedelta64()) <= ts.to_datetime64()
        idx = closed.nonzero()[0]
        if len(idx) == 0 or idx[-1] < n_prev + 1:
            return FLAT
        return int(dir15[idx[-1]])

    ema5 = bars5_today["close"].ewm(span=cfg.entry_ema, adjust=False).mean().to_numpy()
    close5 = bars5_today["close"].to_numpy()

    high5 = bars5_today["high"].to_numpy()
    low5 = bars5_today["low"].to_numpy()

    def _entry_fires(i: int, direction: int) -> bool:
        if i < 1:
            return False
        if cfg.entry_mode == "orb":
            if orb_hi <= 0:
                return False
            if direction == LONG:
                return close5[i - 1] <= orb_hi < close5[i] and high5[i] > orb_hi
            return close5[i - 1] >= orb_lo > close5[i] and low5[i] < orb_lo
        pe, ce, pc, cc = ema5[i - 1], ema5[i], close5[i - 1], close5[i]
        if abs(cc - ce) / max(ce, 1.0) * 100.0 > cfg.max_extension_pct:
            return False
        if direction == LONG:
            return pc <= pe and cc > ce
        return pc >= pe and cc < ce

    def _close(exit_ts: Any, exit_px: float, reason: str) -> None:
        nonlocal pos, day_loss_pts
        if pos is None:
            return
        pts = (exit_px - pos["entry"]) * pos["dir"]
        gross = pts * cfg.lot_size
        cost = futures_round_trip_rupees(pos["entry"], cfg.lot_size, cfg.key) + futures_slippage_rupees(
            cfg.lot_size, cfg.key
        )
        net = gross - cost
        day_loss_pts += -min(0.0, pts)
        trades.append(
            {
                "instrument": cfg.key,
                "direction": "LONG" if pos["dir"] == LONG else "SHORT",
                "entry_time": str(pos["entry_ts"]),
                "entry": round(pos["entry"], 2),
                "exit_time": str(exit_ts),
                "exit": round(exit_px, 2),
                "exit_reason": reason,
                "points": round(pts, 2),
                "gross_rupees": round(gross, 2),
                "friction_rupees": round(cost, 2),
                "net_rupees": round(net, 2),
                "aligned": net > 0,
            }
        )
        pos = None

    for i in range(len(bars5_today)):
        row = bars5_today.iloc[i]
        ts = t5.iloc[i]
        o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))

        # 1. fill anything pending at this bar's open
        if pending is not None and pos is None:
            pos = {"dir": pending["dir"], "entry": o, "entry_ts": ts,
                   "peak": o, "stop": o - pending["dir"] * cfg.initial_stop_pts, "armed": False}
            pending = None
            n_entries += 1
        elif pending is not None and pending.get("exit"):
            _close(ts, o, pending["reason"])
            pending = None

        # 2. manage an open position on this 5m bar
        if pos is not None:
            d = pos["dir"]
            # trailing stop
            fav = (h - pos["entry"]) if d == LONG else (pos["entry"] - low)
            pos["peak"] = max(pos["peak"], h) if d == LONG else min(pos["peak"], low)
            if not pos["armed"] and fav >= cfg.trail_activate_pts:
                pos["armed"] = True
            if pos["armed"]:
                trail = pos["peak"] - d * cfg.trail_pts
                pos["stop"] = max(pos["stop"], trail) if d == LONG else min(pos["stop"], trail)
            # stop touched intrabar?
            stop_hit = (low <= pos["stop"]) if d == LONG else (h >= pos["stop"])
            if stop_hit:
                slip = futures_slippage_rupees(cfg.lot_size, cfg.key) / cfg.lot_size / 2
                _close(ts, pos["stop"] - d * slip, "stop")
                continue
            # square-off
            if ts.time() >= cfg.square_off:
                _close(ts, c, "square_off")
                continue

        # 3. trend + entry decisions
        direction = _dir_at(ts)

        if pos is not None:
            if direction != pos["dir"]:
                pending = {"exit": True, "reason": "15m trend flip"}
            continue

        in_window = cfg.entry_start <= ts.time() <= cfg.entry_window_end
        capped = cfg.max_trades_per_session and n_entries >= cfg.max_trades_per_session
        st5_ok = st5_dir is None or (i < len(st5_dir) and int(st5_dir[i]) == direction)
        if (direction == FLAT or not in_window or capped or not day_range_ok
                or not st5_ok or day_loss_pts >= cfg.daily_stop_pts):
            continue
        if _entry_fires(i, direction):
            pending = {"dir": direction}

    if pos is not None:
        last = bars5_today.iloc[-1]
        _close(t5.iloc[-1], float(last["close"]), "session_end")
    return trades


def run(instrument_key: str, bars5: pd.DataFrame, bars15: pd.DataFrame, *, cfg: FuturesConfig | None = None,
        sessions: int = 0) -> list[dict[str, Any]]:
    cfg = cfg or config_for(instrument_key)
    b5, b15 = bars5.copy(), bars15.copy()
    b5["_d"], b15["_d"] = _day(b5), _day(b15)
    days = sorted(set(b15["_d"]) & set(b5["_d"]))
    if sessions:
        days = days[-sessions:]
    out: list[dict[str, Any]] = []
    for idx in range(1, len(days)):
        d, prev = days[idx], days[idx - 1]
        s5 = b5[b5["_d"] == d].drop(columns="_d").reset_index(drop=True)
        s15 = b15[b15["_d"] == d].drop(columns="_d").reset_index(drop=True)
        p15 = b15[b15["_d"] == prev].drop(columns="_d").reset_index(drop=True)
        if s5.empty or s15.empty or p15.empty:
            continue
        for t in replay_futures_session(s5, s15, p15, cfg):
            t["session"] = str(d)
            out.append(t)
    return out
