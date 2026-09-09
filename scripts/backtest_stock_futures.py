"""Directional-futures signal on individual NSE stock futures.

The identical CPR + EMA9/21 + Supertrend10,3 signal has already been tested on
the three indices via spot-replay and lost — friction was ~10x the gross edge
(NIFTY -Rs63k, BANKNIFTY -Rs186k, SENSEX -Rs82k over ~2yr). The one thing never
tested: whether single-stock intraday range-vs-friction economics change that.

Cash-equity spot-replay is a fair proxy for the stock future (near-month basis is
small and decays to zero by expiry), net of the real NSE stock-futures charge
schedule. Point-based risk fields are scaled to each stock's daily ATR
(index-tuned points don't transfer across price levels).

    python -m scripts.backtest_stock_futures                    # full universe, 24 months
    python -m scripts.backtest_stock_futures --only RELIANCE INFY --months 3
    python -m scripts.backtest_stock_futures --half-spread-bps 2.5
    python -m scripts.backtest_stock_futures --selfcheck        # no network

Writes research/stock_futures/{summary.json,report.md} (gitignored, feeds the
dashboard) and index_ai/strategies/futures/STOCK_RESULTS.md (the committed verdict).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OUT = Path("research/stock_futures")
VERDICT_MD = Path("index_ai/strategies/futures/STOCK_RESULTS.md")
WINDOW_DAYS = 85  # Dhan /charts/intraday serves ~90 calendar days per request
SWEEP_BPS = (0.5, 1.0, 1.5, 2.5)


def _instrument(sym: str, security_id: int, lot: int):
    from index_ai.instruments import IndexInstrument

    return IndexInstrument(
        key=sym.upper(), label=sym.upper(), underlying_security_id=int(security_id),
        underlying_segment="NSE_EQ", instrument_type="EQUITY",
        option_segment="NSE_FNO", strike_step=1, lot_size=int(lot),
        trail_activation_points=0.0, trail_distance_points=0.0, initial_stop_points=0.0,
    )


def _ensure_cached(client, inst, interval: str, months: int, sleep: float) -> None:
    """Walk Dhan's ~90-day intraday window back `months`, upsert each chunk into
    the candle cache (key = symbol — no collision with the 3 index keys). The
    +5:30 shift + session filter happen later, on read in `load_cached_range`."""
    from index_ai.candle_cache import ingest_frame
    from index_ai.dhan import chart_response_to_frame

    now = datetime.now(IST)
    start_limit = now - timedelta(days=round(months * 30.4))
    end = now
    while end > start_limit:
        start = max(start_limit, end - timedelta(days=WINDOW_DAYS))
        try:
            raw = client.intraday_history(
                inst,
                from_date=start.strftime("%Y-%m-%d 09:15:00"),
                to_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                interval=interval,
            )
            frame = chart_response_to_frame(raw)
            if not frame.empty:
                ingest_frame(inst.key, interval, frame)
        except Exception as exc:  # noqa: BLE001 — keep walking past a bad window
            print(f"  {inst.key} {interval}m {start.date()}..{end.date()} failed: {exc}")
        end = start - timedelta(seconds=1)
        time.sleep(max(0.0, sleep))


def _daily_atr_for(client, inst, months: int) -> float:
    from index_ai.dhan import chart_response_to_frame
    from index_ai.strategies.futures.stock_config import daily_atr

    now = datetime.now(IST)
    raw = client.historical_daily(
        inst,
        from_date=(now - timedelta(days=round(months * 30.4))).strftime("%Y-%m-%d"),
        to_date=now.strftime("%Y-%m-%d"),
    )
    return daily_atr(chart_response_to_frame(raw))


_ATR_CACHE: dict[str, float] = {}


def _backtest_one(sym: str, meta: dict, client, months: int, bps: float, sessions: int,
                  *, sleep: float, fetch: bool) -> tuple[dict[str, Any], list[dict]]:
    """(per-stock agg + context, trade list) for one symbol at one half-spread.
    ``fetch`` walks Dhan for candles; the half-spread sweep passes ``fetch=False``
    and reads the cache the first pass populated."""
    from index_ai.candle_cache import load_cached_range
    from index_ai.strategies.futures.backtest import run
    from index_ai.strategies.futures.stock_config import (
        K_DAILY_STOP, K_INITIAL_STOP, K_TRAIL, K_TRAIL_ACTIVATE, stock_config,
    )
    from scripts.backtest_futures import _agg

    inst = _instrument(sym, meta["security_id"], meta["lot_size"])
    if fetch:
        _ensure_cached(client, inst, "5", months, sleep)
        _ensure_cached(client, inst, "15", months, sleep)
    b5 = load_cached_range(inst.key, "5")
    b15 = load_cached_range(inst.key, "15")
    if b5.empty or b15.empty:
        return {"trades": 0, "net_rupees": 0, "note": "no candles"}, []

    if sym not in _ATR_CACHE:
        _ATR_CACHE[sym] = _daily_atr_for(client, inst, months) if fetch else 0.0
    atr = _ATR_CACHE[sym]
    median_price = float(b5["close"].median())
    cfg = stock_config(sym, meta["lot_size"], atr)
    half_spread_pts = round(bps / 1e4 * median_price, 2)
    os.environ[f"SLIPPAGE_FUT_HALF_SPREAD_POINTS_{sym.upper()}"] = str(half_spread_pts)

    trades = run(sym, b5, b15, cfg=cfg, sessions=sessions)
    agg = _agg(trades)
    agg.update(
        atr_daily=round(atr, 1),
        median_price=round(median_price, 1),
        initial_stop_pts=cfg.initial_stop_pts,
        half_spread_pts=half_spread_pts,
        k=[K_INITIAL_STOP, K_TRAIL_ACTIVATE, K_TRAIL, K_DAILY_STOP],
        span=(f"{trades[0]['session']} .. {trades[-1]['session']}" if trades else "-"),
    )
    return agg, trades


def _run_universe(symbols, meta_all, client, months, bps, sessions, *, sleep, fetch=True):
    from scripts.backtest_futures import _agg

    per_stock: dict[str, Any] = {}
    all_trades: list[dict] = []
    for sym in symbols:
        if sym not in meta_all:
            print(f"  {sym}: not in memory/stock_universe.json — skip")
            continue
        if fetch:
            print(f"  {sym} ...", flush=True)
        agg, trades = _backtest_one(
            sym, meta_all[sym], client, months, bps, sessions, sleep=sleep, fetch=fetch
        )
        per_stock[sym] = agg
        all_trades.extend(trades)
    all_trades.sort(key=lambda t: (str(t.get("session")), str(t.get("entry_time"))))
    return per_stock, _agg(all_trades), all_trades


def _half_split(trades: list[dict]) -> tuple[float, float]:
    """(net first half, net second half) by session date."""
    if not trades:
        return 0.0, 0.0
    days = sorted({str(t["session"]) for t in trades})
    mid = days[len(days) // 2]
    first = sum(t["net_rupees"] for t in trades if str(t["session"]) < mid)
    second = sum(t["net_rupees"] for t in trades if str(t["session"]) >= mid)
    return round(first), round(second)


def _verdict(portfolio: dict, per_stock: dict, halves: tuple[float, float],
             sweep: dict[float, float]) -> tuple[bool, str]:
    net = portfolio.get("net_rupees", 0)
    gross = portfolio.get("gross_rupees", 0) or 0
    fric = portfolio.get("friction_rupees", 0) or 0
    ratio = (fric / gross) if gross > 0 else float("inf")
    exps = sorted(v.get("expectancy", 0) for v in per_stock.values() if v.get("trades"))
    median_exp = exps[len(exps) // 2] if exps else 0
    sweep_ok = all(v > 0 for v in sweep.values())
    ok = net > 0 and halves[0] > 0 and halves[1] > 0 and ratio < 0.5 and median_exp > 0 and sweep_ok
    reason = (
        f"net Rs{net:,} | halves Rs{halves[0]:,} / Rs{halves[1]:,} | "
        f"friction/gross {ratio:.1f}x | median per-stock expectancy Rs{median_exp:,} | "
        f"half-spread sweep {'all positive' if sweep_ok else 'flips sign'}"
    )
    return ok, reason


def _write_reports(params, per_stock, portfolio, all_trades, sweep):
    OUT.mkdir(parents=True, exist_ok=True)
    halves = _half_split(all_trades)
    ok, reason = _verdict(portfolio, per_stock, halves, sweep)
    summary = {
        "params": params, "portfolio": portfolio, "per_stock": per_stock,
        "halves": {"first": halves[0], "second": halves[1]},
        "half_spread_sweep": {str(k): v for k, v in sweep.items()},
        "verdict": {"edge": ok, "reason": reason},
        "generated_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    rows = sorted(per_stock.items(), key=lambda kv: kv[1].get("net_rupees", 0), reverse=True)
    lines = [
        "# Stock-futures intraday backtest",
        "",
        f"Generated {summary['generated_at_ist']} · "
        f"window {params['months']}m · half-spread {params['half_spread_bps']} bp/side · "
        f"K {params['k']}",
        "",
        "Cash-equity spot-replay, net of the NSE stock-futures charge schedule. "
        "Equal-capital, one lot per stock.",
        "",
        f"**Verdict: {'EDGE — proceed' if ok else 'NO / too fragile'}** — {reason}",
        "",
        "| Symbol | Span | Trades | L/S | Win% | PF | Net ₹ | Gross ₹ | Friction ₹ | ₹/trade | MaxDD ₹ | ATR | ½-sprd pts |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for sym, a in rows:
        lines.append(
            f"| {sym} | {a.get('span','-')} | {a.get('trades',0)} | "
            f"{a.get('long',0)}/{a.get('short',0)} | {a.get('win_rate_pct',0)} | "
            f"{a.get('profit_factor','—')} | {a.get('net_rupees',0):,} | "
            f"{a.get('gross_rupees',0):,} | {a.get('friction_rupees',0):,} | "
            f"{a.get('expectancy',0):,} | {a.get('max_drawdown',0):,} | "
            f"{a.get('atr_daily','-')} | {a.get('half_spread_pts','-')} |"
        )
    p = portfolio
    lines += [
        f"| **PORTFOLIO** | | {p.get('trades',0)} | {p.get('long',0)}/{p.get('short',0)} | "
        f"{p.get('win_rate_pct',0)} | {p.get('profit_factor','—')} | **{p.get('net_rupees',0):,}** | "
        f"{p.get('gross_rupees',0):,} | {p.get('friction_rupees',0):,} | {p.get('expectancy',0):,} | "
        f"{p.get('max_drawdown',0):,} | | |",
        "",
        f"Per year: {p.get('net_by_year', {})}",
        f"First half ₹{halves[0]:,} · second half ₹{halves[1]:,}",
        "",
        "## Half-spread sensitivity (portfolio net ₹)",
        "",
        "| bp/side | " + " | ".join(f"{k}" for k in SWEEP_BPS) + " |",
        "|---|" + "--:|" * len(SWEEP_BPS),
        "| net ₹ | " + " | ".join(f"{sweep.get(k, 0):,}" for k in SWEEP_BPS) + " |",
        "",
        "## Context — the index verdict this is measured against",
        "",
        "Same signal, spot-replay, ~2yr: NIFTY −₹62,827 · BANKNIFTY −₹186,275 · "
        "SENSEX −₹81,932. Friction ≈ 10× the gross edge. Positional also failed.",
    ]
    text = "\n".join(lines) + "\n"
    (OUT / "report.md").write_text(text, encoding="utf-8")
    VERDICT_MD.write_text(text, encoding="utf-8")
    print(f"\nwrote {OUT}/report.md and {VERDICT_MD}")
    print(f"\nVERDICT: {'EDGE — proceed' if ok else 'NO / too fragile'} — {reason}")


def _selfcheck() -> None:
    import numpy as np
    import pandas as pd

    from index_ai.strategies.futures.backtest import run
    from index_ai.strategies.futures.stock_config import stock_config

    n = 75  # a full 09:15–15:30 session at 5m
    d1 = pd.date_range("2026-09-07 09:15", periods=n, freq="5min")
    d2 = pd.date_range("2026-09-08 09:15", periods=n, freq="5min")
    # day1 flat-ish (sets a low CPR), day2 a rising sawtooth that keeps re-crossing its EMA
    base1 = np.full(n, 1400.0) + np.sin(np.linspace(0, 6, n)) * 2
    base2 = np.linspace(1405, 1445, n) + np.sin(np.linspace(0, 30, n)) * 3
    px = np.concatenate([base1, base2])
    dt = list(d1) + list(d2)
    b5 = pd.DataFrame({"datetime": dt, "open": px, "high": px + 1.5, "low": px - 1.5, "close": px})
    b15 = (
        b5.set_index("datetime")
        .resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna().reset_index()
    )
    cfg = stock_config("TEST", 500, 18.0)
    trades = run("TEST", b5, b15, cfg=cfg)
    assert isinstance(trades, list) and trades, f"expected trades, got {trades}"
    keys = {"instrument", "direction", "entry_time", "entry", "exit_time", "exit",
            "exit_reason", "points", "gross_rupees", "friction_rupees", "net_rupees",
            "aligned", "session"}
    for t in trades:
        assert keys <= set(t), sorted(set(t) ^ keys)
        assert abs(t["net_rupees"] - (t["gross_rupees"] - t["friction_rupees"])) < 1.0
    print(f"backtest_stock_futures self-check ok — {len(trades)} synthetic trades, keys + net math verified")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--only", nargs="*", default=None, metavar="SYM")
    ap.add_argument("--half-spread-bps", type=float, default=1.5, dest="bps")
    ap.add_argument("--sessions", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()

    if args.selfcheck:
        _selfcheck()
        return

    from index_ai.config import settings
    from index_ai.dhan import DhanClient
    from index_ai.strategies.futures.stock_universe import (
        STOCK_FUTURES_UNIVERSE, load_universe_meta,
    )

    cfg = settings()
    if not cfg.dhan.ready:
        raise SystemExit("Dhan token not ready — log in via the dashboard first.")
    client = DhanClient(cfg.dhan)
    meta_all = load_universe_meta()
    symbols = [s.upper() for s in (args.only or STOCK_FUTURES_UNIVERSE)]

    print(f"stock-futures backtest — {len(symbols)} names, {args.months}m, {args.bps} bp/side")
    per_stock, portfolio, all_trades = _run_universe(
        symbols, meta_all, client, args.months, args.bps, args.sessions, sleep=args.sleep
    )

    sweep: dict[float, float] = {args.bps: portfolio.get("net_rupees", 0)}
    if not args.no_sweep:
        for bp in SWEEP_BPS:
            if bp == args.bps:
                continue
            print(f"  sweep {bp} bp ...", flush=True)
            _, pf, _ = _run_universe(
                symbols, meta_all, client, args.months, bp, args.sessions,
                sleep=args.sleep, fetch=False,
            )
            sweep[bp] = pf.get("net_rupees", 0)

    params = {"months": args.months, "half_spread_bps": args.bps, "sessions": args.sessions,
              "names": symbols, "k": per_stock[symbols[0]].get("k") if symbols and symbols[0] in per_stock else None}
    _write_reports(params, per_stock, portfolio, all_trades, sweep)


if __name__ == "__main__":
    main()
