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
from dataclasses import replace
from typing import Any

import pandas as pd

from commodities.charges import round_trip_cost_rupees, slippage_rupees
from commodities.config import CommoditySettings, commodity_settings, signal_config
from commodities.instruments import BY_KEY, CommoditySpec, candle_instrument, load_universe_meta
from commodities.session import entries_open, is_mcx_trading_day, mcx_day, past_squareoff
from index_ai import notify
from index_ai.atomic_io import atomic_write_json
from index_ai.config import MEMORY_DIR, settings
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.market_clock import now_ist, now_ist_iso
from index_ai.strategies.futures.engine import FLAT, LONG, entry_trigger, trend_read
from index_ai.strategies.futures.price_trail import PriceTrailLevels, update_price_trail

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
    atomic_write_json(STATE_PATH, state)


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


def _fetch(
    client: DhanClient, spec: CommoditySpec, security_id: int, interval: str
) -> pd.DataFrame:
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


# ---- ATR-scaled risk ------------------------------------------------------
#
# Richard, 2026-09-12: "each script has its own way of moving... for gold the
# 0.45 is very small but for silver it may be very big... we need the
# flexibility to save capital [without] restricting the stop loss to a
# certain percentage which may be less for some scripts but way more for
# others." Measured (2026-09-12, 90 real MCX daily bars, median daily true
# range as % of price): CRUDEOILM ~3.98%, NATGASMINI ~3.06%, SILVERMIC
# ~2.45%, GOLDM ~1.56% — a 2.5x spread between the calmest and the wildest,
# so one flat percentage across all four was always going to be wrong for at
# least some of them.
#
# The ratios below are not new or unproven — they're the same point-to-ATR
# ratios the stock-futures lane already runs live with
# (index_ai/strategies/futures/stock_config.py), just applied to each
# commodity's own measured *percent* volatility instead of absolute points
# (gold at ~₹1,50,000 and gas at ~₹270 aren't comparable in rupees).
ATR_K_INITIAL_STOP = 0.22
ATR_K_TRAIL_ACTIVATE = 0.22
ATR_K_TRAIL = 0.40
ATR_K_DAILY_STOP = 0.55
# phase 2 -- a tighter profit-lock once the trade has run well past where
# phase 1 armed (Richard, 2026-09-15: every segment needs a real
# trailing-stop AND a separate trailing-profit phase). Same ~2.5x / ~0.35x
# ratios as CommoditySpec's static fallback values and the index-futures
# _RISK table this mirrors.
ATR_K_PROFIT_TRIGGER = 0.55
ATR_K_PROFIT_TRAIL = 0.14
_ATR_TTL_S = 24 * 3600.0  # volatility drifts slowly; no need to re-measure every scan
_atr_cache: dict[str, tuple[float, float]] = {}  # key -> (measured_at_monotonic, atr_pct)


def _measure_daily_atr_pct(
    spec: CommoditySpec, security_id: int, client: DhanClient
) -> float | None:
    """Median daily true range as % of that day's close, over ~90 calendar
    days of real MCX daily candles. None on any failure — callers fall back
    to a stale cached value, or the spec's own static guess; never raises."""
    now = now_ist()
    try:
        raw = client.historical_daily(
            candle_instrument(spec, security_id),
            from_date=(now - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
            to_date=now.strftime("%Y-%m-%d"),
        )
        df = chart_response_to_frame(raw)
    except Exception:
        return None
    if df is None or df.empty or len(df) < 10:
        return None
    prev_close = df["close"].shift()
    tr = (
        pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        )
        .max(axis=1)
        .dropna()
    )
    if tr.empty:
        return None
    pct = float((tr / df["close"]).median() * 100.0)
    return pct if pct > 0 else None


def atr_scaled_spec(spec: CommoditySpec, security_id: int, client: DhanClient) -> CommoditySpec:
    """``spec`` with its four risk-percent fields replaced by ones scaled off
    this contract's own measured daily volatility, instead of the one
    hand-picked guess every instrument shared before. Cached 24h. Falls back
    to the last successful measurement, or to ``spec`` unchanged if one has
    never succeeded — never blocks a scan over a Dhan hiccup."""
    hit = _atr_cache.get(spec.key)
    now = _time.monotonic()
    atr = hit[1] if hit and now - hit[0] < _ATR_TTL_S else None
    if atr is None:
        measured = _measure_daily_atr_pct(spec, security_id, client)
        atr = measured if measured is not None else (hit[1] if hit else None)
        if atr is not None:
            _atr_cache[spec.key] = (now, atr)
    if atr is None:
        return spec
    return replace(
        spec,
        initial_stop_pct=round(ATR_K_INITIAL_STOP * atr, 3),
        trail_activate_pct=round(ATR_K_TRAIL_ACTIVATE * atr, 3),
        trail_pct=round(ATR_K_TRAIL * atr, 3),
        daily_stop_pct=round(ATR_K_DAILY_STOP * atr, 3),
        profit_trigger_pct=round(ATR_K_PROFIT_TRIGGER * atr, 3),
        profit_trail_pct=round(ATR_K_PROFIT_TRAIL * atr, 3),
    )


def _sessions(df: pd.DataFrame) -> dict[Any, pd.DataFrame]:
    if df.empty:
        return {}
    work = df.copy()
    work["_d"] = pd.to_datetime(work["datetime"]).dt.date
    return {d: g.drop(columns="_d").reset_index(drop=True) for d, g in work.groupby("_d")}


# ---- position management ------------------------------------------------


def _close(
    pos: dict[str, Any],
    px: float,
    reason: str,
    spec: CommoditySpec,
    lots: int,
    state: dict[str, Any],
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
        "peak_price": round(pos.get("peak", pos["entry"]), 2),
        "trail_armed": pos.get("armed", False),
        "profit_armed": pos.get("profit_armed", False),
        "trail_stop_at_exit": round(pos["stop"], 2) if pos.get("stop") is not None else None,
    }
    _journal(trade)
    try:
        notify.commodity_closed(trade)
    except Exception:
        pass
    slot = state.setdefault(spec.key, {})
    slot["position"] = None
    slot["last_exit_at"] = trade["exit_time"]
    return trade


def _manage(
    pos: dict[str, Any], price: float, spec: CommoditySpec, tr_direction: int
) -> str | None:
    d = 1 if pos["dir"] == "LONG" else -1
    entry = pos["entry"]
    levels = PriceTrailLevels(
        trail_activate_pts=entry * spec.trail_activate_pct / 100.0,
        trail_pts=entry * spec.trail_pct / 100.0,
        profit_trigger_pts=entry * spec.profit_trigger_pct / 100.0,
        profit_trail_pts=entry * spec.profit_trail_pct / 100.0,
    )
    if update_price_trail(pos, price, levels):
        return "stop"
    if tr_direction not in (d, FLAT):
        return "trend_flip"
    return None


def _todays_trades_for(key: str, day: str) -> int:
    return sum(1 for t in _recent(300) if t.get("instrument") == key and str(t.get("day")) == day)


# ---- one instrument ----------------------------------------------------


def tick(
    client: DhanClient,
    spec: CommoditySpec,
    security_id: int,
    s: CommoditySettings,
    state: dict[str, Any],
    open_total: int,
) -> dict[str, Any]:
    key = spec.key
    spec = atr_scaled_spec(
        spec, security_id, client
    )  # this instrument's own measured risk, not a flat guess
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
        "instrument": key,
        "dir": "LONG" if d == LONG else "SHORT",
        "entry": price,
        "entry_time": now_ist_iso(),
        "day": day,
        "peak": price,
        "stop": price - d * price * spec.initial_stop_pct / 100.0,
        "armed": False,
        "trend_reason": tr.reason,
        "mode": "PAPER",
    }
    slot["position"] = pos
    try:
        notify.commodity_opened(pos, spec.label, s.lots)
    except Exception:
        pass
    ev.update(event="entry", position=pos)
    return ev


# ---- the scan ---------------------------------------------------------


def scan_commodities_paper(client: DhanClient | None = None) -> list[dict[str, Any]]:
    s = commodity_settings()
    if not s.enabled:
        return []
    if not is_mcx_trading_day():  # weekend/holiday — skip the Dhan calls entirely, not just entries
        return []
    meta = load_universe_meta()
    if not meta:
        return [
            {"event": "error", "where": "universe", "error": "run scripts.fetch_commodity_universe"}
        ]
    client = client or DhanClient(settings().dhan)  # DhanClient is stateless — nothing to close
    try:
        state = _load_state()
        open_total = sum(1 for v in state.values() if isinstance(v, dict) and v.get("position"))
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


def _mark_price(client: DhanClient, spec: CommoditySpec, security_id: int | None) -> float | None:
    """Last 5m close for one contract — reuses the same cached fetch the scan
    loop uses, so this costs nothing extra on top of the running lane."""
    if not security_id:
        return None
    try:
        frame = _fetch(client, spec, security_id, "5")
        return float(frame["close"].iloc[-1]) if not frame.empty else None
    except Exception:
        return None


def _contract_status(key: str, meta: dict[str, Any], client: DhanClient) -> dict[str, Any]:
    row = meta.get(key) or {}
    spec = BY_KEY.get(key)
    if not spec:
        return {"label": key}
    effective = (
        atr_scaled_spec(spec, row.get("security_id"), client) if row.get("security_id") else spec
    )
    return {
        "label": spec.label,
        "expiry": row.get("expiry"),
        "trading_symbol": row.get("trading_symbol"),
        "multiplier": spec.multiplier,
        "initial_stop_pct": effective.initial_stop_pct,
        "trail_activate_pct": effective.trail_activate_pct,
        "trail_pct": effective.trail_pct,
        "daily_stop_pct": effective.daily_stop_pct,
        "risk_source": "measured" if effective is not spec else "default",
    }


def commodities_status() -> dict[str, Any]:
    s = commodity_settings()
    state = _load_state()
    trades = _recent(200)
    today = now_ist().date().isoformat()
    todays = [t for t in trades if str(t.get("exit_time", ""))[:10] == today]
    meta = load_universe_meta()

    client = DhanClient(settings().dhan)
    open_positions: dict[str, Any] = {}
    open_unrealized = 0.0
    for k, v in state.items():
        pos = v.get("position") if isinstance(v, dict) else None
        if not pos:
            continue
        pos = dict(pos)
        spec = BY_KEY.get(k)
        mark = _mark_price(client, spec, (meta.get(k) or {}).get("security_id")) if spec else None
        if mark is not None and spec:
            d = 1 if pos.get("dir") == "LONG" else -1
            points = (mark - float(pos["entry"])) * d
            pnl = points * spec.multiplier * s.lots
            pos["mark"] = round(mark, 2)
            pos["unrealized_rupees"] = round(pnl, 2)
            pos["unrealized_pct"] = (
                round(points / float(pos["entry"]) * 100.0, 3) if pos.get("entry") else None
            )
            open_unrealized += pnl
        else:  # no live mark — show the position but not a fake ₹0 P&L
            pos["mark"] = None
            pos["unrealized_rupees"] = None
            pos["unrealized_pct"] = None
        open_positions[k] = pos

    return {
        "enabled": s.enabled,
        "symbols": list(s.symbols),
        "lots": s.lots,
        "contracts": {k: _contract_status(k, meta, client) for k in s.symbols},
        "open_positions": open_positions,
        "today": {
            "closed": len(todays),
            "net_rupees": round(sum(float(t["net_rupees"]) for t in todays), 2),
            "wins": sum(1 for t in todays if float(t["net_rupees"]) > 0),
            "open_unrealized_rupees": round(open_unrealized, 2),
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

    # ATR scaling: a calmer instrument (small measured %) must come out with
    # a tighter stop than a wilder one (large measured %) -- the whole point.
    calm, wild = BY_KEY["GOLDM"], BY_KEY["CRUDEOILM"]
    _atr_cache["GOLDM"] = (_time.monotonic(), 1.56)  # ~measured 2026-09-12
    _atr_cache["CRUDEOILM"] = (_time.monotonic(), 3.98)
    calm_scaled = atr_scaled_spec(calm, 1, client=None)  # cache hit -> no network
    wild_scaled = atr_scaled_spec(wild, 2, client=None)
    assert calm_scaled.initial_stop_pct < wild_scaled.initial_stop_pct
    assert calm_scaled.initial_stop_pct == round(ATR_K_INITIAL_STOP * 1.56, 3)
    # a measurement that never succeeds falls back to the static default untouched
    assert atr_scaled_spec(BY_KEY["SILVERMIC"], None, client=None) == BY_KEY["SILVERMIC"]
    print("commodities.lanes self-check ok (disabled -> no-op; ATR scaling sane)")
