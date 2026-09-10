"""MCX commodity-futures paper lane.

`scan_commodities_paper()` is called ~60 s from the server's commodity task. For
each configured contract it pulls recent 5m + 15m MCX candles, runs the index
directional signal (`futures.engine`), and journals paper LONG/SHORT positions
with the MCX charge schedule applied. Risk is percent-of-price (a CRUDEOILM point
and a GOLDM point are nothing alike). Never raises; never places an order.

State: `memory/commodity_state.json`. Closed trades: `memory/commodity_journal.jsonl`.
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from typing import Any

import pandas as pd

from commodities.charges import round_trip_cost_rupees, slippage_rupees
from commodities.config import CommoditySettings, commodity_settings, signal_config
from commodities.instruments import BY_KEY, CommoditySpec, candle_instrument, load_universe_meta
from commodities.session import entries_open, mcx_day, past_squareoff
from index_ai.config import MEMORY_DIR, settings
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.market_clock import now_ist, now_ist_iso
from index_ai.strategies.futures.engine import FLAT, LONG, entry_trigger, trend_read

logger = logging.getLogger(__name__)

STATE_PATH = MEMORY_DIR / "commodity_state.json"
JOURNAL_PATH = MEMORY_DIR / "commodity_journal.jsonl"

_FRAME_TTL_S = 240.0
_frame_cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}


def enabled() -> bool:
    return commodity_settings().enabled


# ---- persistence ----------------------------------------------------------

def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, default=str)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    for attempt in range(6):  # Windows: AV / sync agents briefly lock the target
        try:
            os.replace(tmp, STATE_PATH)
            return
        except PermissionError:
            if attempt == 5:
                break
            _time.sleep(0.25)
    STATE_PATH.write_text(payload, encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass


def _journal(trade: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade, default=str) + "\n")


def _recent(limit: int = 200) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.is_file():
        return []
    lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[-limit:] if x.strip()]


# ---- data ---------------------------------------------------------------

def _to_ist(frame: pd.DataFrame) -> pd.DataFrame:
    """Dhan sends naive-UTC epochs. Shift a whole-day MCX frame onto IST
    wall-clock (unlike index candle_cache, keep the full 09:00-23:30 span)."""
    if frame.empty:
        return frame
    work = frame.copy()
    work["datetime"] = pd.to_datetime(work["datetime"])
    if work["datetime"].dt.time.min() < pd.Timestamp("07:00").time():
        work["datetime"] = work["datetime"] + pd.Timedelta(hours=5, minutes=30)
    return work.sort_values("datetime").reset_index(drop=True)


def _fetch(client: DhanClient, spec: CommoditySpec, security_id: int, interval: str) -> pd.DataFrame:
    ck = (spec.key, interval)
    hit = _frame_cache.get(ck)
    if hit and _time.monotonic() - hit[0] < _FRAME_TTL_S:
        return hit[1]
    now = now_ist()
    inst = candle_instrument(spec, security_id)
    raw = client.intraday_history(
        inst,
        from_date=(now - pd.Timedelta(days=4)).strftime("%Y-%m-%d 09:00:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
    )
    frame = _to_ist(chart_response_to_frame(raw))
    _frame_cache[ck] = (_time.monotonic(), frame)
    return frame


def _sessions(df: pd.DataFrame) -> dict[Any, pd.DataFrame]:
    if df.empty:
        return {}
    work = df.copy()
    work["_d"] = pd.to_datetime(work["datetime"]).dt.date
    return {d: g.drop(columns="_d").reset_index(drop=True) for d, g in work.groupby("_d")}


# ---- position management ------------------------------------------------

def _close(
    pos: dict[str, Any], px: float, reason: str, spec: CommoditySpec, lots: int, state: dict[str, Any]
) -> dict[str, Any]:
    d = 1 if pos["dir"] == "LONG" else -1
    points = (px - pos["entry"]) * d
    gross = points * spec.multiplier * lots
    cost = round_trip_cost_rupees(pos["entry"], px, spec, lots) + slippage_rupees(spec, lots)
    trade = {
        "instrument": spec.key,
        "label": spec.label,
        "mode": "PAPER",
        "direction": pos["dir"],
        "lots": lots,
        "multiplier": spec.multiplier,
        "entry_time": pos["entry_time"],
        "entry": round(pos["entry"], 2),
        "exit_time": now_ist_iso(),
        "exit": round(px, 2),
        "exit_reason": reason,
        "points": round(points, 2),
        "gross_rupees": round(gross, 2),
        "friction_rupees": round(cost, 2),
        "net_rupees": round(gross - cost, 2),
        "day": pos.get("day"),
        "lane": "commodities",
        "trend_reason": pos.get("trend_reason"),
    }
    _journal(trade)
    slot = state.setdefault(spec.key, {})
    slot["position"] = None
    slot["last_exit_at"] = trade["exit_time"]
    return trade


def _manage(pos: dict[str, Any], price: float, spec: CommoditySpec, tr_direction: int) -> str | None:
    d = 1 if pos["dir"] == "LONG" else -1
    fav = (price - pos["entry"]) * d
    pos["peak"] = max(pos["peak"], price) if d == 1 else min(pos["peak"], price)
    activate = pos["entry"] * spec.trail_activate_pct / 100.0
    if not pos["armed"] and fav >= activate:
        pos["armed"] = True
    if pos["armed"]:
        trail_pts = pos["entry"] * spec.trail_pct / 100.0
        trail = pos["peak"] - d * trail_pts
        pos["stop"] = max(pos["stop"], trail) if d == 1 else min(pos["stop"], trail)
    if (price <= pos["stop"]) if d == 1 else (price >= pos["stop"]):
        return "stop"
    if tr_direction not in (d, FLAT):
        return "trend_flip"
    return None


def _todays_trades_for(key: str, day: str) -> int:
    return sum(1 for t in _recent(300) if t.get("instrument") == key and str(t.get("day")) == day)


# ---- one instrument ----------------------------------------------------

def tick(
    client: DhanClient, spec: CommoditySpec, security_id: int, s: CommoditySettings,
    state: dict[str, Any], open_total: int,
) -> dict[str, Any]:
    key = spec.key
    ev: dict[str, Any] = {"instrument": key, "event": "none"}
    slot = state.setdefault(key, {"position": None})
    pos = slot.get("position")
    day = mcx_day()

    try:
        s5 = _fetch(client, spec, security_id, "5")
        s15 = _fetch(client, spec, security_id, "15")
    except Exception as exc:
        return {"instrument": key, "event": "fetch_error", "error": str(exc)}
    if s5.empty or s15.empty:
        return {"instrument": key, "event": "no_data"}

    d5, d15 = _sessions(s5), _sessions(s15)
    days = sorted(set(d5) & set(d15))
    if len(days) < 2:
        return {"instrument": key, "event": "need_two_sessions"}
    today5, today15, prev15 = d5[days[-1]], d15[days[-1]], d15[days[-2]]
    price = float(today5["close"].iloc[-1])
    cfg = signal_config()
    tr = trend_read(pd.concat([prev15, today15], ignore_index=True), prev15, cfg)

    if pos:
        reason = None
        if past_squareoff(spec.dst_session):
            reason = "square_off"
        else:
            reason = _manage(pos, price, spec, tr.direction)
        if reason:
            px = pos["stop"] if reason == "stop" else price
            ev.update(event="exit", trade=_close(pos, px, reason, spec, s.lots, state))
        else:
            slot["position"] = pos
            ev["event"] = "hold"
        return ev

    # entry
    if not entries_open(spec.dst_session):
        ev["reason"] = "outside MCX entry window"
        return ev
    if s.max_open_total and open_total >= s.max_open_total:
        ev["reason"] = f"portfolio cap {s.max_open_total} open"
        return ev
    if s.max_trades_per_day and _todays_trades_for(key, day) >= s.max_trades_per_day:
        ev["reason"] = f"max {s.max_trades_per_day} trades today"
        return ev
    if tr.direction == FLAT:
        ev["reason"] = "no trend"
        return ev
    fired, why = entry_trigger(today5, tr.direction, cfg)
    if not fired:
        ev["reason"] = why
        return ev

    d = tr.direction
    pos = {
        "dir": "LONG" if d == LONG else "SHORT",
        "entry": price,
        "entry_time": now_ist_iso(),
        "day": day,
        "peak": price,
        "stop": price - d * price * spec.initial_stop_pct / 100.0,
        "armed": False,
        "trend_reason": tr.reason,
    }
    slot["position"] = pos
    ev.update(event="entry", position=pos)
    return ev


# ---- the scan ---------------------------------------------------------

def scan_commodities_paper(client: DhanClient | None = None) -> list[dict[str, Any]]:
    s = commodity_settings()
    if not s.enabled:
        return []
    meta = load_universe_meta()
    if not meta:
        return [{"event": "error", "where": "universe",
                 "error": "run scripts.fetch_commodity_universe"}]
    client = client or DhanClient(settings().dhan)  # DhanClient is stateless — nothing to close
    try:
        state = _load_state()
        open_total = sum(
            1 for v in state.values() if isinstance(v, dict) and v.get("position")
        )
        events: list[dict[str, Any]] = []
        for key in s.symbols:
            spec = BY_KEY.get(key)
            row = meta.get(key)
            if not spec or not row:
                events.append({"instrument": key, "event": "unresolved"})
                continue
            try:
                e = tick(client, spec, int(row["security_id"]), s, state, open_total)
            except Exception as exc:  # one instrument failing must not stop the rest
                e = {"instrument": key, "event": "error", "error": str(exc)}
            if e.get("event") == "entry":
                open_total += 1
            elif e.get("event") == "exit":
                open_total = max(0, open_total - 1)
            events.append(e)
        _save_state(state)
        return events
    except Exception as exc:
        logger.warning("commodities scan aborted", exc_info=True)
        return [{"event": "error", "where": "scan", "error": str(exc)}]


def commodities_status() -> dict[str, Any]:
    s = commodity_settings()
    state = _load_state()
    trades = _recent(200)
    today = now_ist().date().isoformat()
    todays = [t for t in trades if str(t.get("exit_time", ""))[:10] == today]
    meta = load_universe_meta()
    return {
        "enabled": s.enabled,
        "symbols": list(s.symbols),
        "lots": s.lots,
        "contracts": {
            k: {
                "label": BY_KEY[k].label if k in BY_KEY else k,
                "expiry": (meta.get(k) or {}).get("expiry"),
                "trading_symbol": (meta.get(k) or {}).get("trading_symbol"),
                "multiplier": BY_KEY[k].multiplier if k in BY_KEY else None,
            }
            for k in s.symbols
        },
        "open_positions": {
            k: v.get("position")
            for k, v in state.items()
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
        "recent_trades": trades[::-1][:30],
        "generated_at_ist": now_ist_iso(),
    }


if __name__ == "__main__":  # self-check — a fully-disabled lane is a no-op
    os.environ["ENABLE_COMMODITIES_PAPER"] = "false"
    assert scan_commodities_paper() == []
    assert not enabled()
    print("commodities.lanes self-check ok (disabled -> no-op)")
