"""
Live paper-trading for the directional index-futures strategy.

Runs inside the scanner loop (behind ENABLE_FUTURES_PAPER). Each tick pulls recent
5m + 15m spot candles, runs the same engine as the backtest, and journals paper
LONG/SHORT positions. State in memory/futures_paper.json, closed trades in
memory/futures_journal.jsonl. Deliberately separate from the options executor /
journal, which are option-legs shaped.

Signal + "fill" price use spot (futures track it with a small, decaying basis) —
good enough for forward paper data.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from index_ai.config import MEMORY_DIR
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.instruments import get_instrument
from index_ai.market_clock import now_ist, now_ist_iso
from index_ai.candle_cache import to_ist_session_frame
from index_ai.charges import futures_round_trip_rupees, futures_slippage_rupees
from index_ai.strategies.futures.config import FuturesConfig, config_for, with_overrides
from index_ai.strategies.futures.engine import FLAT, LONG, entry_trigger, trend_read

IST = ZoneInfo("Asia/Kolkata")
STATE_PATH = MEMORY_DIR / "futures_paper.json"
JOURNAL_PATH = MEMORY_DIR / "futures_journal.jsonl"


def enabled() -> bool:
    return os.getenv("ENABLE_FUTURES_PAPER", "true").strip().lower() in {"1", "true", "yes", "on"}


def instruments() -> list[str]:
    from index_ai.instruments import index_paused

    raw = os.getenv("FUTURES_PAPER_INSTRUMENTS", "NIFTY,BANKNIFTY,SENSEX")
    return [
        x.strip().upper()
        for x in raw.split(",")
        if x.strip() and not index_paused(x.strip())
    ]


def _cfg(key: str) -> FuturesConfig:
    cfg = config_for(key)
    ov: dict[str, Any] = {}
    for field in cfg.__dataclass_fields__:
        env = os.getenv(f"FUT_{field.upper()}")
        if env is None:
            continue
        cur = getattr(cfg, field)
        try:
            ov[field] = type(cur)(env) if not isinstance(cur, bool) else env.lower() in {"1", "true", "yes"}
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


def _fetch(client: DhanClient, key: str, interval: str, days: int = 2) -> pd.DataFrame:
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
    df = df.copy()
    df["_d"] = pd.to_datetime(df["datetime"]).dt.date
    return {d: g.drop(columns="_d").reset_index(drop=True) for d, g in df.groupby("_d")}


def _close(pos: dict[str, Any], px: float, reason: str, cfg: FuturesConfig,
           state: dict[str, Any]) -> dict[str, Any]:
    d = 1 if pos["dir"] == "LONG" else -1
    pts = (px - pos["entry"]) * d
    gross = pts * cfg.lot_size
    cost = futures_round_trip_rupees(pos["entry"], cfg.lot_size, cfg.key) + futures_slippage_rupees(
        cfg.lot_size, cfg.key
    )
    trade = {
        "instrument": cfg.key,
        "mode": "PAPER",
        "direction": pos["dir"],
        "lot_size": cfg.lot_size,
        "entry_time": pos["entry_time"],
        "entry": round(pos["entry"], 2),
        "exit_time": now_ist_iso(),
        "exit": round(px, 2),
        "exit_reason": reason,
        "points": round(pts, 2),
        "gross_rupees": round(gross, 2),
        "friction_rupees": round(cost, 2),
        "net_rupees": round(gross - cost, 2),
        "lane": "futures",
        "brain": pos.get("brain"),
    }
    _journal(trade)
    state.setdefault(cfg.key, {})["position"] = None
    state[cfg.key]["last_exit_at"] = trade["exit_time"]
    return trade


def _brain_check(trade_like: dict[str, Any], today5, prev5, prev_day_ohlc) -> dict[str, Any]:
    """Regime + learned win-probability gate. Falls open on any failure so a broken
    brain can never stop the lane."""
    try:
        from index_ai.brain.gate import check
        from index_ai.brain.regime import classify
        from index_ai.strategies.strategy import previous_day_cpr

        pivot, bc, tc = previous_day_cpr(prev_day_ohlc)
        width_pct = (tc - bc) / max(pivot, 1.0) * 100.0
        read = classify(today5, prev5, cpr_width_pct=width_pct) if prev5 is not None else None
        return check(trade_like, lane="futures", regime=read)
    except Exception as exc:
        return {"allowed": True, "reason": f"brain unavailable: {exc}", "win_probability": None}


def tick(client: DhanClient, key: str, state: dict[str, Any]) -> dict[str, Any]:
    cfg = _cfg(key)
    out: dict[str, Any] = {"instrument": key, "event": "none"}
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
    today5, today15, prev15 = d5[today], d15[today], d15[prev]

    price = float(today5["close"].iloc[-1])
    ts = now_ist()
    inst_state = state.setdefault(key, {"position": None})
    pos = inst_state.get("position")

    full15 = pd.concat([prev15, today15], ignore_index=True)
    tr = trend_read(full15, prev15, cfg)

    # manage open position
    if pos:
        d = 1 if pos["dir"] == "LONG" else -1
        fav = (price - pos["entry"]) * d
        pos["peak"] = max(pos["peak"], price) if d == 1 else min(pos["peak"], price)
        if not pos["armed"] and fav >= cfg.trail_activate_pts:
            pos["armed"] = True
        if pos["armed"]:
            trail = pos["peak"] - d * cfg.trail_pts
            pos["stop"] = max(pos["stop"], trail) if d == 1 else min(pos["stop"], trail)
        if (price <= pos["stop"]) if d == 1 else (price >= pos["stop"]):
            out.update(event="exit", trade=_close(pos, pos["stop"], "stop", cfg, state))
            return out
        if ts.time() >= cfg.square_off:
            out.update(event="exit", trade=_close(pos, price, "square_off", cfg, state))
            return out
        if tr.direction not in (d, FLAT):
            out.update(event="exit", trade=_close(pos, price, "trend_flip", cfg, state))
            return out
        inst_state["position"] = pos
        out["event"] = "hold"
        return out

    # look for an entry
    if tr.direction == FLAT or not (cfg.entry_start <= ts.time() <= cfg.entry_window_end):
        out["reason"] = "flat" if tr.direction == FLAT else "outside window"
        return out
    fired, why = entry_trigger(today5, tr.direction, cfg)
    if not fired:
        out["reason"] = why
        return out
    d = tr.direction
    pos = {
        "dir": "LONG" if d == LONG else "SHORT",
        "entry": price,
        "entry_time": now_ist_iso(),
        "peak": price,
        "stop": price - d * cfg.initial_stop_pts,
        "armed": False,
        "trend_reason": tr.reason,
    }
    verdict = _brain_check(
        {**pos, "instrument": key, "lane": "futures",
         "direction": pos["dir"], "entry_spot": price},
        today5, d5.get(prev), prev15,
    )
    if not verdict["allowed"]:
        out["reason"] = f"brain: {verdict['reason']}"
        return out
    pos["brain"] = verdict
    inst_state["position"] = pos
    out["event"] = "entry"
    out["position"] = pos
    out["brain"] = verdict
    return out


def scan_futures_paper(client: DhanClient) -> list[dict[str, Any]]:
    if not enabled():
        return []
    state = _load_state()
    events = [tick(client, key, state) for key in instruments()]
    _save_state(state)
    return events


def _recent_trades(limit: int = 50) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.is_file():
        return []
    lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[-limit:] if x.strip()][::-1]


def futures_paper_status() -> dict[str, Any]:
    state = _load_state()
    trades = _recent_trades(200)
    today = now_ist().date().isoformat()
    todays = [t for t in trades if str(t.get("exit_time", ""))[:10] == today]
    return {
        "enabled": enabled(),
        "instruments": instruments(),
        "open_positions": {
            k: v.get("position") for k, v in state.items() if isinstance(v, dict) and v.get("position")
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
