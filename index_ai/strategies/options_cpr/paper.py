"""
Live paper-trading for the CPR + EMA option-buying strategy.

Runs inside the scanner loop (behind ENABLE_OPTIONS_CPR_PAPER). Same signal +
state machine as the backtest; the option premium is the same Black-Scholes
proxy off spot (``premium.py``) — real Dhan option LTP is the obvious upgrade if
premium-level accuracy matters. State in memory/options_cpr_paper.json, closed
trades in memory/options_cpr_journal.jsonl. Separate from the options-sell
executor / journal.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

import pandas as pd

from index_ai.candle_cache import to_ist_session_frame
from index_ai.charges import half_spread_points, leg_charge_rupees
from index_ai.config import MEMORY_DIR
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.instruments import get_instrument
from index_ai.market_clock import now_ist, now_ist_iso
from index_ai.strategies.options_cpr.config import OptionsCprConfig, config_for, with_overrides
from index_ai.strategies.options_cpr.engine import add_indicators, cpr_context, evaluate_entry
from index_ai.strategies.options_cpr.premium import premium_at, select_strike

STATE_PATH = MEMORY_DIR / "options_cpr_paper.json"
JOURNAL_PATH = MEMORY_DIR / "options_cpr_journal.jsonl"
_MINS = 375.0


def enabled() -> bool:
    return os.getenv("ENABLE_OPTIONS_CPR_PAPER", "false").strip().lower() in {"1", "true", "yes", "on"}


def instruments() -> list[str]:
    raw = os.getenv("OPTIONS_CPR_PAPER_INSTRUMENTS", "NIFTY,BANKNIFTY")
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _cfg(key: str) -> OptionsCprConfig:
    cfg = config_for(key)
    ov: dict[str, Any] = {}
    for field in cfg.__dataclass_fields__:
        env = os.getenv(f"OCPR_{field.upper()}")
        if env is None:
            continue
        cur = getattr(cfg, field)
        try:
            ov[field] = env.lower() in {"1", "true", "yes"} if isinstance(cur, bool) else type(cur)(env)
        except Exception:
            pass
    return with_overrides(cfg, **ov) if ov else cfg


def _load_state() -> dict[str, Any]:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _journal(trade: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade, default=str) + "\n")


def _fetch(client: DhanClient, key: str, interval: str, days: int = 4) -> pd.DataFrame:
    inst = get_instrument(key)
    now = now_ist()
    start = now - timedelta(days=days)
    raw = client.intraday_history(
        inst,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
    )
    return to_ist_session_frame(chart_response_to_frame(raw))


def _sessions(df: pd.DataFrame) -> dict[Any, pd.DataFrame]:
    if df.empty:
        return {}
    d = df.copy()
    d["_d"] = pd.to_datetime(d["datetime"]).dt.date
    return {k: g.drop(columns="_d").reset_index(drop=True) for k, g in d.groupby("_d")}


def _day_counters(state: dict[str, Any], key: str, today: str) -> dict[str, Any]:
    c = state.setdefault(key, {}).get("day")
    if not c or c.get("date") != today:
        c = {"date": today, "trades": 0, "consec_losses": 0, "daily_pnl": 0.0, "kill": False}
        state[key]["day"] = c
    return c


def _friction(entry_prem: float, exit_prem: float, qty: int, cfg: OptionsCprConfig) -> float:
    return (
        leg_charge_rupees(entry_prem, qty, "BUY", exchange=cfg.exchange)
        + leg_charge_rupees(exit_prem, qty, "SELL", exchange=cfg.exchange)
        + half_spread_points(cfg.key) * max(1, qty) * 2
    )


def _mte(cfg: OptionsCprConfig, ts, open_ts) -> float:
    elapsed = (pd.Timestamp(ts) - pd.Timestamp(open_ts)).total_seconds() / 60.0
    return max(0.0, cfg.assumed_days_to_expiry * _MINS - elapsed)


def _dir_15m(prev15: pd.DataFrame, today15: pd.DataFrame, cfg: OptionsCprConfig) -> int:
    """+1 / -1 / 0 — EMA-fast vs EMA-slow on the last closed 15m bar (carry prev day for warm-up)."""
    full = pd.concat([prev15, today15], ignore_index=True)
    if len(full) < cfg.ema_slow:
        return 0
    ef = full["close"].ewm(span=cfg.ema_fast, adjust=False).mean().iloc[-1]
    es = full["close"].ewm(span=cfg.ema_slow, adjust=False).mean().iloc[-1]
    return 1 if ef > es else -1


def _close(pos: dict[str, Any], exit_prem: float, reason: str, spot: float,
           cfg: OptionsCprConfig, ctr: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    qty = pos["qty_open"]
    gross = (exit_prem - pos["entry_premium"]) * qty
    fric = _friction(pos["entry_premium"], exit_prem, qty, cfg)
    net = gross - fric
    ctr["daily_pnl"] = round(ctr["daily_pnl"] + net, 2)
    if net > 0:
        ctr["consec_losses"] = 0
    else:
        ctr["consec_losses"] += 1
    if ctr["consec_losses"] >= cfg.max_consecutive_losses:
        ctr["kill"] = True
    trade = {
        "instrument": cfg.key, "mode": "PAPER", "premium_model": "bs_proxy",
        "side": pos["side"], "strike": pos["strike"], "stage": pos["stage"], "exit_reason": reason,
        "entry_time": pos["entry_time"], "exit_time": now_ist_iso(),
        "entry_spot": round(pos["entry_spot"], 2), "exit_spot": round(spot, 2),
        "entry_premium": round(pos["entry_premium"], 2), "exit_premium": round(exit_prem, 2),
        "qty": qty, "gross_rupees": round(gross, 2), "friction_rupees": round(fric, 2),
        "net_rupees": round(net, 2),
    }
    _journal(trade)
    state[cfg.key]["position"] = None
    return trade


def tick(client: DhanClient, key: str, state: dict[str, Any]) -> dict[str, Any]:
    cfg = _cfg(key)
    try:
        s5 = _fetch(client, key, "5")
        s15 = _fetch(client, key, "15")
    except Exception as exc:
        return {"instrument": key, "event": "fetch_error", "error": str(exc)}
    if s5.empty or s15.empty:
        return {"instrument": key, "event": "no_data"}

    d5, d15 = _sessions(s5), _sessions(s15)
    days = sorted(set(d5) & set(d15))
    if len(days) < 2:
        return {"instrument": key, "event": "need_two_sessions"}
    today, prev = days[-1], days[-2]
    today5, prev5 = d5[today], d5[prev]

    d15_dir = _dir_15m(d15[prev], d15[today], cfg)  # last-closed 15m EMA alignment
    cpr = cpr_context(prev5, cfg)
    tail = prev5.tail(cfg.warmup_bars + 5)
    df = add_indicators(pd.concat([tail, today5], ignore_index=True), cfg)
    i = len(df) - 1
    row = df.iloc[i]
    ts = now_ist()
    spot = float(row["close"])
    open_ts = pd.to_datetime(today5["datetime"]).iloc[0]

    inst_state = state.setdefault(key, {"position": None})
    ctr = _day_counters(state, key, str(today))
    pos = inst_state.get("position")
    out: dict[str, Any] = {"instrument": key, "event": "none"}

    if pos:
        p_now = premium_at(spot, pos["strike"], pos["is_call"], cfg.iv, _mte(cfg, ts, open_ts))
        e, r = pos["entry_premium"], pos["r_unit"]
        pos["peak_premium"] = max(pos["peak_premium"], p_now)
        pos["peak_spot"] = max(pos["peak_spot"], spot) if pos["is_call"] else min(pos["peak_spot"], spot)
        peak = pos["peak_premium"]
        if pos["stage"] < 1 and peak >= e + cfg.trail_stage1_trigger_r * r:
            pos["stage"], pos["sl_premium"] = 1, max(pos["sl_premium"], e)
        if pos["stage"] < 2 and peak >= e + cfg.trail_stage2_trigger_r * r:
            pos["stage"], pos["sl_premium"] = 2, max(pos["sl_premium"], e + 0.5 * r)
        if pos["stage"] < 3 and peak >= e + cfg.trail_stage3_trigger_r * r:
            pos["stage"] = 3

        broke_struct = (spot < cpr.tc) if pos["is_call"] else (spot > cpr.bc)
        trail_hit = False
        if pos["stage"] >= 3:
            atr = float(row["atr"])
            trail_spot = (pos["peak_spot"] - cfg.atr_multiplier * atr) if pos["is_call"] else (
                pos["peak_spot"] + cfg.atr_multiplier * atr)
            ref = max(trail_spot, float(row["ema_fast"])) if pos["is_call"] else min(
                trail_spot, float(row["ema_fast"]))
            trail_hit = (spot < ref) if pos["is_call"] else (spot > ref)

        if p_now <= pos["sl_premium"]:
            out.update(event="exit", trade=_close(pos, pos["sl_premium"], "trail_sl" if pos["stage"] else "premium_sl", spot, cfg, ctr, state))
        elif broke_struct:
            out.update(event="exit", trade=_close(pos, p_now, "structural_sl", spot, cfg, ctr, state))
        elif trail_hit:
            out.update(event="exit", trade=_close(pos, p_now, "atr_ema_trail", spot, cfg, ctr, state))
        elif not pos["partial_booked"] and p_now >= pos["target_premium"] and pos["qty_open"] > 1:
            half = max(1, int(pos["qty_open"] * cfg.partial_book_fraction))
            book = dict(pos, qty_open=half)
            tr = _close(book, pos["target_premium"], "partial_target", spot, cfg, ctr, state)
            pos["qty_open"] -= half
            pos["partial_booked"] = True
            pos["sl_premium"] = max(pos["sl_premium"], e + 0.5 * r)
            pos["stage"] = max(pos["stage"], 3)
            state[key]["position"] = pos
            out.update(event="partial", trade=tr)
        elif p_now >= pos["target_premium"]:
            out.update(event="exit", trade=_close(pos, pos["target_premium"], "target", spot, cfg, ctr, state))
        elif d15_dir != 0 and d15_dir != (1 if pos["is_call"] else -1):
            out.update(event="exit", trade=_close(pos, p_now, "trend_flip_15m", spot, cfg, ctr, state))
        elif ts.time() >= cfg.square_off_time:
            out.update(event="exit", trade=_close(pos, p_now, "square_off", spot, cfg, ctr, state))
        else:
            state[key]["position"] = pos
            out["event"] = "hold"
        return out

    # entry
    if ctr["kill"] or ctr["trades"] >= cfg.max_trades_per_day:
        out["reason"] = "kill switch" if ctr["kill"] else "max trades/day"
        return out
    cap = cfg.daily_loss_cap_rupees or (cfg.max_daily_loss_pct / 100.0 * cfg.capital)
    if ctr["daily_pnl"] <= -cap:
        out["reason"] = "daily loss circuit breaker"
        return out
    if not (cfg.first_entry_time <= ts.time() <= cfg.last_entry_time):
        out["reason"] = "outside entry window"
        return out
    side, why = evaluate_entry(df, i, cpr, cfg)
    if side is None:
        out["reason"] = why
        return out
    is_call = side == "CE"
    if d15_dir != 0 and d15_dir != (1 if is_call else -1):
        out["reason"] = "15m EMA not aligned"
        return out

    strike = select_strike(spot, cfg.strike_step, is_call, cfg.strike_selection)
    mte = _mte(cfg, ts, open_ts)
    entry_prem = premium_at(spot, strike, is_call, cfg.iv, mte)
    if entry_prem <= 1.0:
        out["reason"] = "premium model returned ~0"
        return out
    struct_level = cpr.tc if is_call else cpr.bc
    sl_premium = max(premium_at(struct_level, strike, is_call, cfg.iv, mte),
                     entry_prem * (1.0 - cfg.initial_sl_premium_pct / 100.0))
    max_loss = cfg.max_loss_pct_of_utilized_capital / 100.0 * entry_prem * cfg.lot_size
    if (entry_prem - sl_premium) * cfg.lot_size > max_loss:
        sl_premium = entry_prem - max_loss / cfg.lot_size
    r_unit = entry_prem - sl_premium
    if r_unit < entry_prem * 0.02:
        out["reason"] = "capital cap forces stop too tight"
        return out

    pos = {
        "side": side, "is_call": is_call, "strike": strike, "entry_spot": spot,
        "entry_premium": entry_prem, "entry_time": now_ist_iso(), "sl_premium": sl_premium,
        "r_unit": r_unit, "target_premium": entry_prem + cfg.risk_reward_ratio * r_unit,
        "qty_open": cfg.lot_size, "stage": 0, "peak_premium": entry_prem, "peak_spot": spot,
        "partial_booked": False,
    }
    inst_state["position"] = pos
    ctr["trades"] += 1
    out.update(event="entry", position=pos, reason=why)
    return out


def scan_options_cpr_paper(client: DhanClient) -> list[dict[str, Any]]:
    if not enabled():
        return []
    state = _load_state()
    events = [tick(client, key, state) for key in instruments()]
    _save_state(state)
    return events


def _recent_trades(limit: int = 60) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.is_file():
        return []
    lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[-limit:] if x.strip()][::-1]


def options_cpr_paper_status() -> dict[str, Any]:
    state = _load_state()
    trades = _recent_trades(300)
    today = now_ist().date().isoformat()
    todays = [t for t in trades if str(t.get("exit_time", ""))[:10] == today]
    return {
        "enabled": enabled(),
        "instruments": instruments(),
        "open_positions": {
            k: v.get("position") for k, v in state.items()
            if isinstance(v, dict) and v.get("position")
        },
        "today": {
            "closed": len(todays),
            "net_rupees": round(sum(float(t["net_rupees"]) for t in todays), 2),
            "wins": sum(1 for t in todays if float(t["net_rupees"]) > 0),
        },
        "all_time": {
            "closed": len(trades),
            "net_rupees": round(sum(float(t["net_rupees"]) for t in trades), 2),
        },
        "recent_trades": trades[:30],
        "generated_at_ist": now_ist_iso(),
    }
