"""Backtest the prior-day range failed-breakout reversal
(`index_ai.strategies.range_break`) on index futures and MCX commodity futures.

    python -m scripts.backtest_range_break                       # index, from cache
    python -m scripts.backtest_range_break --commodities         # + MCX (90d, live fetch)
    python -m scripts.backtest_range_break --interval 5 --buffer 0.04

Index futures replay cached spot 5m candles (small, decaying basis — same
approximation the directional-futures backtest uses). Commodities fetch ~90 days
of MCX 5m from Dhan. Real charge schedules applied. Paper research only — nothing
is wired to a lane.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, timedelta
from typing import Any

import pandas as pd

from index_ai.strategies.range_break import RangeBreakConfig, replay_day

OUT = __import__("pathlib").Path("memory/backtests")
INDEX = ["NIFTY", "BANKNIFTY", "SENSEX"]
COMMODITIES = ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERMIC"]


def _sessions(df: pd.DataFrame) -> list[tuple[date, pd.DataFrame]]:
    if df.empty:
        return []
    work = df.copy()
    work["_d"] = pd.to_datetime(work["datetime"]).dt.date
    return [(d, g.drop(columns="_d").reset_index(drop=True)) for d, g in work.groupby("_d")]


def _summarise(rows: list[dict[str, Any]], currency: str = "INR") -> dict[str, Any]:
    if not rows:
        return {"trades": 0}
    nets = [r["net"] for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    eq = peak = dd = 0.0
    for n in nets:
        eq += n
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    rrs = [r["rr"] for r in rows if r.get("rr")]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(rows), 3),
        "net": round(sum(nets), 2),
        "gross": round(sum(r["gross"] for r in rows), 2),
        "cost": round(sum(r["cost"] for r in rows), 2),
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
        "expectancy": round(sum(nets) / len(rows), 2),
        "max_dd": round(dd, 2),
        "avg_rr": round(statistics.mean(rrs), 2) if rrs else 0.0,
        "by_reason": {
            k: sum(1 for r in rows if r["reason"] == k)
            for k in {r["reason"] for r in rows}
        },
        "currency": currency,
    }


def _replay_instrument(
    key: str, frame: pd.DataFrame, cfg: RangeBreakConfig, cost_fn, gross_fn
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sess = _sessions(frame)
    for i in range(1, len(sess)):
        _, prev = sess[i - 1]
        d, today = sess[i]
        d1h, d1l = float(prev["high"].max()), float(prev["low"].min())
        for f in replay_day(d1h, d1l, today, cfg):
            gross = gross_fn(f["points"])
            cost = cost_fn(f["entry"], f["exit"])
            out.append({
                "instrument": key, "day": d.isoformat(), "dir": f["dir"],
                "entry": round(f["entry"], 2), "exit": round(f["exit"], 2),
                "points": round(f["points"], 2), "rr": f.get("rr", 0.0),
                "reason": f["reason"], "gross": round(gross, 2), "cost": round(cost, 2),
                "net": round(gross - cost, 2),
            })
    return out


def run_index(interval: str, cfg: RangeBreakConfig) -> dict[str, Any]:
    from index_ai.candle_cache import load_cached_range
    from index_ai.charges import futures_round_trip_rupees, futures_slippage_rupees
    from index_ai.instruments import market_lot_size

    res: dict[str, Any] = {}
    for key in INDEX:
        frame = load_cached_range(key, interval)
        if frame.empty:
            print(f"{key}: no {interval}m cache — skip")
            continue
        lot = market_lot_size(key)
        rows = _replay_instrument(
            key, frame, cfg,
            cost_fn=lambda e, x, k=key, lt=lot: futures_round_trip_rupees(e, lt, k)
            + futures_slippage_rupees(lt, k),
            gross_fn=lambda pts, lt=lot: pts * lt,
        )
        res[key] = {"summary": _summarise(rows), "trades": rows}
        s = res[key]["summary"]
        span = f"{rows[0]['day']}..{rows[-1]['day']}" if rows else "—"
        print(f"  {key:10s} ({span}) {s['trades']:4d} trades  {s['win_rate']*100:.0f}% win  "
              f"net Rs {s['net']:>12,.0f}  exp Rs {s['expectancy']:>7,.0f}  avgRR {s['avg_rr']}")
    return res


def run_commodities(interval: str, cfg: RangeBreakConfig, days: int) -> dict[str, Any]:
    from commodities.charges import round_trip_cost_rupees, slippage_rupees
    from commodities.instruments import BY_KEY, candle_instrument, load_universe_meta
    from index_ai.config import settings
    from index_ai.dhan import DhanClient, chart_response_to_frame
    from index_ai.market_clock import now_ist

    meta = load_universe_meta()
    if not meta:
        print("commodities: run `python -m scripts.fetch_commodity_universe` first")
        return {}
    client = DhanClient(settings().dhan)
    now = now_ist()
    res: dict[str, Any] = {}
    for key in COMMODITIES:
        spec, row = BY_KEY.get(key), meta.get(key)
        if not spec or not row:
            continue
        try:
            raw = client.intraday_history(
                candle_instrument(spec, int(row["security_id"])),
                from_date=(now - timedelta(days=days)).strftime("%Y-%m-%d 09:00:00"),
                to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                interval=interval,
            )
        except Exception as exc:
            print(f"  {key}: fetch failed — {exc}")
            continue
        frame = chart_response_to_frame(raw)
        if not frame.empty and pd.to_datetime(frame["datetime"]).dt.time.min() < pd.Timestamp("07:00").time():
            frame["datetime"] = pd.to_datetime(frame["datetime"]) + pd.Timedelta(hours=5, minutes=30)
        rows = _replay_instrument(
            key, frame, cfg,
            cost_fn=lambda e, x, sp=spec: round_trip_cost_rupees(e, x, sp, 1) + slippage_rupees(sp, 1),
            gross_fn=lambda pts, sp=spec: pts * sp.multiplier,
        )
        res[key] = {"summary": _summarise(rows), "trades": rows}
        s = res[key]["summary"]
        span = f"{rows[0]['day']}..{rows[-1]['day']}" if rows else "—"
        print(f"  {key:10s} ({span}) {s['trades']:4d} trades  {s['win_rate']*100:.0f}% win  "
              f"net Rs {s['net']:>10,.0f}  exp Rs {s['expectancy']:>7,.0f}  avgRR {s['avg_rr']}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5")
    ap.add_argument("--buffer", type=float, default=0.04, help="break buffer, %% of price")
    ap.add_argument("--cutoff", default="14:30", help="no new setups after this IST time")
    ap.add_argument("--min-rr", type=float, default=0.0)
    ap.add_argument("--max-target-rr", type=float, default=0.0)
    ap.add_argument("--commodities", action="store_true")
    ap.add_argument("--commodity-days", type=int, default=90)
    args = ap.parse_args()

    from datetime import time as _t

    hh, mm = args.cutoff.split(":")
    cfg = RangeBreakConfig(
        break_buffer_pct=args.buffer,
        entry_cutoff=_t(int(hh), int(mm)),
        min_rr=args.min_rr,
        max_target_rr=args.max_target_rr,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"range-break backtest — {args.interval}m, buffer {args.buffer}%, cutoff {args.cutoff}\n")
    print("INDEX FUTURES (cached spot replay):")
    out: dict[str, Any] = {"config": vars(args), "index": run_index(args.interval, cfg)}
    if args.commodities:
        print("\nMCX COMMODITY FUTURES (last %d days, live fetch):" % args.commodity_days)
        out["commodities"] = run_commodities(args.interval, cfg, args.commodity_days)
    (OUT / "range_break.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwritten: {OUT / 'range_break.json'}")


if __name__ == "__main__":
    main()
