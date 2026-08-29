"""
Spot-replay backtest for the CPR + EMA option-buying strategy (spec Sections 4-9).

There is no historical option chain, so the option premium over each trade is a
Black-Scholes proxy off the spot path (see ``premium.py``). Treat the *signal*
stats (win rate on the breakout, trade count, hold time) as meaningful and the
rupee P&L as indicative only — calibrate against the live paper journal.

Fills:
  * entry / target / square-off / trend-flip -> at the modelled premium on that 5m close
  * premium or trailing stop hit             -> at the stop's premium level (adverse intrabar)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.charges import half_spread_points, leg_charge_rupees
from index_ai.strategies.options_cpr.config import OptionsCprConfig, config_for
from index_ai.strategies.options_cpr.engine import add_indicators, cpr_context, evaluate_entry
from index_ai.strategies.options_cpr.premium import premium_at, select_strike

_MINS = 375.0  # trading minutes per session
_15M = pd.Timedelta("15min")


def _friction(entry_prem: float, exit_prem: float, qty: int, cfg: OptionsCprConfig) -> float:
    charges = leg_charge_rupees(entry_prem, qty, "BUY", exchange=cfg.exchange) + leg_charge_rupees(
        exit_prem, qty, "SELL", exchange=cfg.exchange
    )
    slippage = half_spread_points(cfg.key) * max(1, qty) * 2
    return charges + slippage


def _daily_loss_cap(cfg: OptionsCprConfig) -> float:
    if cfg.daily_loss_cap_rupees > 0:
        return cfg.daily_loss_cap_rupees
    return cfg.max_daily_loss_pct / 100.0 * cfg.capital


def _align15(bars15_prev: pd.DataFrame, bars15_today: pd.DataFrame, cfg: OptionsCprConfig):
    full = pd.concat([bars15_prev, bars15_today], ignore_index=True)
    ef = full["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    es = full["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    direction = (ef > es).map({True: 1, False: -1}).to_numpy()
    ts15 = pd.to_datetime(full["datetime"]).to_numpy()

    def at(ts: pd.Timestamp) -> int:
        closed = (ts15 + _15M.to_timedelta64()) <= ts.to_datetime64()
        idx = closed.nonzero()[0]
        return int(direction[idx[-1]]) if len(idx) else 0

    return at


def replay_session(
    bars5_prev_tail: pd.DataFrame,
    bars5_today: pd.DataFrame,
    bars15_prev: pd.DataFrame,
    bars15_today: pd.DataFrame,
    prev_day_ohlc: pd.DataFrame,
    cfg: OptionsCprConfig,
    *,
    require_15m_alignment: bool = True,
) -> list[dict[str, Any]]:
    cpr = cpr_context(prev_day_ohlc, cfg)
    n_tail = len(bars5_prev_tail)
    df = add_indicators(
        pd.concat([bars5_prev_tail, bars5_today], ignore_index=True), cfg
    )
    align15 = _align15(bars15_prev, bars15_today, cfg) if require_15m_alignment else (lambda _ts: 0)

    ts_all = pd.to_datetime(df["datetime"])
    open_ts = ts_all.iloc[n_tail] if len(df) > n_tail else None
    expiry_total_min = cfg.assumed_days_to_expiry * _MINS

    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    consec_losses = 0
    trades_today = 0
    daily_pnl = 0.0
    kill = False
    loss_cap = _daily_loss_cap(cfg)

    def prem(spot: float, ts: pd.Timestamp) -> float:
        mte = max(0.0, expiry_total_min - (ts - open_ts).total_seconds() / 60.0)
        return premium_at(spot, pos["strike"], pos["is_call"], cfg.iv, mte)

    def close_pos(exit_prem: float, qty: int, reason: str, ts: pd.Timestamp, spot: float) -> None:
        nonlocal pos, consec_losses, daily_pnl, kill
        gross = (exit_prem - pos["entry_premium"]) * qty
        fric = _friction(pos["entry_premium"], exit_prem, qty, cfg)
        net = gross - fric
        daily_pnl += net
        trades.append(
            {
                "instrument": cfg.key,
                "side": pos["side"],
                "strike": pos["strike"],
                "stage": pos["stage"],
                "reason": reason,
                "entry_time": str(pos["entry_time"]),
                "exit_time": str(ts),
                "entry_spot": round(pos["entry_spot"], 2),
                "exit_spot": round(spot, 2),
                "entry_premium": round(pos["entry_premium"], 2),
                "exit_premium": round(exit_prem, 2),
                "qty": qty,
                "gross_rupees": round(gross, 2),
                "friction_rupees": round(fric, 2),
                "net_rupees": round(net, 2),
            }
        )
        pos["qty_open"] -= qty
        if pos["qty_open"] <= 0:
            if net > 0:
                consec_losses = 0
            elif reason not in {"partial_target"}:
                consec_losses += 1
            if consec_losses >= cfg.max_consecutive_losses:
                kill = True
            pos = None

    for i in range(n_tail, len(df)):
        row = df.iloc[i]
        ts = ts_all.iloc[i]
        o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))

        # ---- manage an open position ----
        if pos is not None:
            adverse_spot = low if pos["is_call"] else h
            favor_spot = h if pos["is_call"] else low
            p_now = prem(c, ts)
            p_adverse = prem(adverse_spot, ts)
            p_favor = prem(favor_spot, ts)
            pos["peak_premium"] = max(pos["peak_premium"], p_favor)
            e, r = pos["entry_premium"], pos["r_unit"]
            peak = pos["peak_premium"]

            # stage transitions on best premium reached
            if pos["stage"] < 1 and peak >= e + cfg.trail_stage1_trigger_r * r:
                pos["stage"] = 1
                pos["sl_premium"] = max(pos["sl_premium"], e)
            if pos["stage"] < 2 and peak >= e + cfg.trail_stage2_trigger_r * r:
                pos["stage"] = 2
                pos["sl_premium"] = max(pos["sl_premium"], e + 0.5 * r)
            if pos["stage"] < 3 and peak >= e + cfg.trail_stage3_trigger_r * r:
                pos["stage"] = 3

            # structural invalidation — spot closed back through the CPR line
            broke_struct = (c < cpr.tc) if pos["is_call"] else (c > cpr.bc)

            # 1) stops (checked at the adverse intrabar extreme) — conservative: stop wins ties
            trail_hit = False
            if pos["stage"] >= 3:
                atr = float(row["atr"])
                trail_spot = (
                    pos["peak_spot"] - cfg.atr_multiplier * atr
                    if pos["is_call"]
                    else pos["peak_spot"] + cfg.atr_multiplier * atr
                )
                ema_stop = float(row["ema_fast"])
                struct_trail = max(trail_spot, ema_stop) if pos["is_call"] else min(trail_spot, ema_stop)
                trail_hit = (c < struct_trail) if pos["is_call"] else (c > struct_trail)
            pos["peak_spot"] = max(pos["peak_spot"], h) if pos["is_call"] else min(pos["peak_spot"], low)

            if p_adverse <= pos["sl_premium"]:
                close_pos(pos["sl_premium"], pos["qty_open"], "premium_sl" if pos["stage"] < 1 else "trail_sl", ts, adverse_spot)
                continue
            if broke_struct:
                close_pos(p_now, pos["qty_open"], "structural_sl", ts, c)
                continue
            if trail_hit:
                close_pos(p_now, pos["qty_open"], "atr_ema_trail", ts, c)
                continue

            # 2) target / partial booking
            if not pos["partial_booked"] and p_favor >= pos["target_premium"]:
                half = max(1, int(pos["qty_open"] * cfg.partial_book_fraction))
                if half < pos["qty_open"]:
                    close_pos(pos["target_premium"], half, "partial_target", ts, favor_spot)
                    pos["partial_booked"] = True
                    pos["sl_premium"] = max(pos["sl_premium"], e + 0.5 * r)
                    pos["stage"] = max(pos["stage"], 3)
                    continue
                # can't split (already 1 lot) -> book it all at target
                close_pos(pos["target_premium"], pos["qty_open"], "target", ts, favor_spot)
                continue

            # 3) 15m trend flip
            if require_15m_alignment:
                d15 = align15(ts)
                if d15 != 0 and d15 != (1 if pos["is_call"] else -1):
                    close_pos(p_now, pos["qty_open"], "trend_flip_15m", ts, c)
                    continue

            # 4) square-off
            if ts.time() >= cfg.square_off_time:
                close_pos(p_now, pos["qty_open"], "square_off", ts, c)
                continue
            continue

        # ---- look for an entry ----
        if kill or trades_today >= cfg.max_trades_per_day:
            continue
        if loss_cap > 0 and daily_pnl <= -loss_cap:
            continue
        if not (cfg.first_entry_time <= ts.time() <= cfg.last_entry_time):
            continue
        side, _why = evaluate_entry(df, i, cpr, cfg)
        if side is None:
            continue
        is_call = side == "CE"
        if require_15m_alignment:
            d15 = align15(ts)
            if d15 != 0 and d15 != (1 if is_call else -1):
                continue

        strike = select_strike(c, cfg.strike_step, is_call, cfg.strike_selection)
        mte = max(0.0, expiry_total_min - (ts - open_ts).total_seconds() / 60.0)
        entry_prem = premium_at(c, strike, is_call, cfg.iv, mte)
        if entry_prem <= 1.0:
            continue

        struct_level = cpr.tc if is_call else cpr.bc
        sl_struct = premium_at(struct_level, strike, is_call, cfg.iv, mte)
        sl_pct = entry_prem * (1.0 - cfg.initial_sl_premium_pct / 100.0)
        sl_premium = max(sl_struct, sl_pct)  # tighter (higher) stop wins

        # 5%-of-utilised-capital cap (spec Section 4). utilised = premium * lot.
        # loss_at_sl <= 0.05*utilised  =>  sl_premium >= 0.95 * entry_prem.
        max_loss = cfg.max_loss_pct_of_utilized_capital / 100.0 * entry_prem * cfg.lot_size
        if (entry_prem - sl_premium) * cfg.lot_size > max_loss:
            sl_premium = entry_prem - max_loss / cfg.lot_size
        r_unit = entry_prem - sl_premium
        if r_unit < entry_prem * 0.02:
            continue  # capital cap forces a stop too tight to be tradeable

        pos = {
            "side": side,
            "is_call": is_call,
            "strike": strike,
            "entry_spot": c,
            "entry_premium": entry_prem,
            "entry_time": ts,
            "sl_premium": sl_premium,
            "r_unit": r_unit,
            "target_premium": entry_prem + cfg.risk_reward_ratio * r_unit,
            "qty_open": cfg.lot_size,
            "stage": 0,
            "peak_premium": entry_prem,
            "peak_spot": c,
            "partial_booked": False,
        }
        trades_today += 1

    if pos is not None:
        last = df.iloc[-1]
        close_pos(prem(float(last["close"]), ts_all.iloc[-1]), pos["qty_open"], "session_end",
                  ts_all.iloc[-1], float(last["close"]))
    return trades


def run(
    instrument_key: str,
    bars5: pd.DataFrame,
    bars15: pd.DataFrame,
    *,
    cfg: OptionsCprConfig | None = None,
    sessions: int = 0,
    require_15m_alignment: bool = True,
) -> list[dict[str, Any]]:
    cfg = cfg or config_for(instrument_key)
    b5, b15 = bars5.copy(), bars15.copy()
    b5["_d"] = pd.to_datetime(b5["datetime"]).dt.date
    b15["_d"] = pd.to_datetime(b15["datetime"]).dt.date
    days = sorted(set(b5["_d"]))
    if sessions:
        days = days[-sessions:]
    out: list[dict[str, Any]] = []
    for idx in range(1, len(days)):
        d, prev = days[idx], days[idx - 1]
        today5 = b5[b5["_d"] == d].drop(columns="_d").reset_index(drop=True)
        prev5 = b5[b5["_d"] == prev].drop(columns="_d").reset_index(drop=True)
        today15 = b15[b15["_d"] == d].drop(columns="_d").reset_index(drop=True)
        prev15 = b15[b15["_d"] == prev].drop(columns="_d").reset_index(drop=True)
        if today5.empty or prev5.empty or prev15.empty:
            continue
        tail = prev5.tail(cfg.warmup_bars + 5)
        for t in replay_session(tail, today5, prev15, today15, prev5, cfg,
                                require_15m_alignment=require_15m_alignment):
            t["session"] = str(d)
            out.append(t)
    return out
