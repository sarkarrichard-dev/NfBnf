"""
Full-history backtest of every strategy style, written to research/.

Replays each STRATEGY_STYLE over the entire local candle cache (net of the
index_ai.charges friction model), aggregates by year / instrument / lane, and
writes JSON + a markdown report. Also emits a combined trade log for
scripts/research_ml_seed.py.

    python -m scripts.research_backtest                    # AUTO,BUY,CREDIT x NIFTY,BANKNIFTY
    python -m scripts.research_backtest --styles AUTO --instruments NIFTY --sessions 250
    python -m scripts.research_backtest --all              # every style x every instrument, full history

Slow (minutes per style/instrument on a long history). Runs one combo at a time
and writes partial output so it is safe to interrupt and rerun.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

RESEARCH = Path("research")
BACKTESTS = RESEARCH / "backtests"
REPORTS = RESEARCH / "reports"
DATASETS = RESEARCH / "datasets"


def _agg(trades: list[dict]) -> dict:
    pnls = [float(t["proxy_pnl_rupees"]) for t in trades]
    gross = [float(t.get("gross_proxy_pnl_rupees") or t["proxy_pnl_rupees"]) for t in trades]
    friction = [float(t.get("estimated_friction_rupees") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    by_year: dict[str, float] = defaultdict(float)
    by_inst: dict[str, float] = defaultdict(float)
    for t in trades:
        by_year[str(t.get("session") or t.get("entry_time") or "")[:4]] += float(t["proxy_pnl_rupees"])
        by_inst[str(t.get("instrument") or "")] += float(t["proxy_pnl_rupees"])
    n = len(pnls)
    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1) if n else 0.0,
        "net_pnl": round(sum(pnls)),
        "gross_pnl": round(sum(gross)),
        "friction_paid": round(sum(friction)),
        "avg_win": round(sum(wins) / len(wins)) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses)) if losses else 0,
        "expectancy": round(sum(pnls) / n) if n else 0,
        "max_drawdown": round(max_dd),
        "net_by_year": {k: round(v) for k, v in sorted(by_year.items()) if k},
        "net_by_instrument": {k: round(v) for k, v in by_inst.items() if k},
    }


def _write_report(summary: dict) -> None:
    lines = ["# Full-history backtest — every interval × style\n"]
    lines.append("_Net P&L is after the index_ai.charges friction model. Option P&L is a "
                 "delta/theta proxy — calibrate against the paper journal before trusting levels._\n")
    for grp, insts in summary.items():
        lines.append(f"\n## {grp}\n")
        lines.append("| Instrument | Span | Trades | Win% | Net ₹ | Gross ₹ | Friction ₹ | Expectancy ₹ | Max DD ₹ |")
        lines.append("|---|---|--:|--:|--:|--:|--:|--:|--:|")
        for inst_key, a in insts.items():
            lines.append(
                f"| {inst_key} | {a['span']} | {a['trades']} | {a['win_rate_pct']} | "
                f"{a['net_pnl']:,} | {a['gross_pnl']:,} | {a['friction_paid']:,} | "
                f"{a['expectancy']:,} | {a['max_drawdown']:,} |"
            )
        for inst_key, a in insts.items():
            if a.get("net_by_year"):
                yrs = "  ".join(f"{y}: ₹{v:,}" for y, v in a["net_by_year"].items())
                lines.append(f"\n*{inst_key} by year* — {yrs}")
    (REPORTS / "backtest_all_styles.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--styles", nargs="*", default=["AUTO", "BUY", "CREDIT"])
    ap.add_argument("--instruments", nargs="*", default=["NIFTY", "BANKNIFTY"])
    ap.add_argument("--iv", nargs="*", default=["1"], help="candle interval(s): 1 5 15")
    ap.add_argument("--sessions", type=int, default=0, help="most-recent N sessions (0 = all)")
    ap.add_argument("--stride", type=int, default=3,
                    help="evaluate the router every Nth bar (1 = every bar; 3 default for research)")
    ap.add_argument("--hold-credit", action="store_true",
                    help="hold credit spreads to a spot stop / session close instead of exiting on signal flip")
    ap.add_argument("--cooldown", type=int, default=0,
                    help="bars to wait after an exit before a new entry (anti-whipsaw)")
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VAL",
                    help="strategy-param env override, repeatable (e.g. --param EMA_SLOW_PERIOD=10)")
    ap.add_argument("--all", action="store_true", help="every style x SENSEX too, full history")
    args = ap.parse_args()

    if args.hold_credit:
        os.environ["EXIT_CREDIT_ON_SIGNAL_FLIP"] = "false"
    if args.cooldown:
        os.environ["REENTRY_COOLDOWN_BARS"] = str(args.cooldown)
    _overrides = dict(p.split("=", 1) for p in args.param if "=" in p)

    styles = ["AUTO", "BUY", "CREDIT", "APEX"] if args.all else [s.upper() for s in args.styles]
    instruments = (
        ["NIFTY", "BANKNIFTY", "SENSEX"] if args.all else [i.upper() for i in args.instruments]
    )

    for d in (BACKTESTS, REPORTS, DATASETS):
        d.mkdir(parents=True, exist_ok=True)

    from index_ai.backtest import _replay_candles, _sessions
    from index_ai.candle_cache import load_cached_range
    from index_ai.config import freeze_env, settings
    from index_ai.instruments import get_instrument
    from index_ai.strategies.strategy_params import reload_strategy_params
    import pandas as pd

    intervals = [str(x) for x in args.iv]
    combined_trades: list[dict] = []
    summary: dict[str, dict] = {}

    # settings() / candle_interval_minutes() call load_dotenv(override=True), which
    # would clobber the STRATEGY_STYLE / CANDLE_INTERVAL_MINUTES overrides below
    # mid-replay. Read settings once, then pin the environment.
    app = settings()
    freeze_env()

    for iv in intervals:
        for style in styles:
            os.environ["CANDLE_INTERVAL_MINUTES"] = iv
            os.environ["STRATEGY_STYLE"] = style
            for k, v in _overrides.items():
                os.environ[k] = v
            reload_strategy_params()
            for inst_key in instruments:
                combo = f"{iv}m/{style}/{inst_key}"
                candles = load_cached_range(inst_key, iv)
                if candles.empty:
                    print(f"{combo}: no cached candles — skip", flush=True)
                    continue
                if args.sessions:
                    days = [d for d, _ in _sessions(candles)][-args.sessions - 1 :]
                    candles = candles[pd.to_datetime(candles["datetime"]).dt.date.isin(set(days))]
                inst = get_instrument(inst_key)
                print(f"{combo}: replaying {len(_sessions(candles))} sessions...", flush=True)
                trades, _sess, sessions = _replay_candles(
                    candles, instrument=inst, app_settings=app, pnl_mode="option_proxy",
                    signal_stride=args.stride,
                )
                for t in trades:
                    t["style"], t["interval"] = style, iv
                combined_trades.extend(trades)
                a = _agg(trades)
                a["sessions"] = max(0, len(sessions) - 1)
                a["span"] = f"{str(sessions[0][0])} .. {str(sessions[-1][0])}" if sessions else ""
                summary.setdefault(f"{iv}m·{style}", {})[inst_key] = a
                (BACKTESTS / f"{iv}m_{style}_{inst_key}.json").write_text(
                    json.dumps({"summary": a, "trades": trades}, indent=2, default=str),
                    encoding="utf-8",
                )
                (BACKTESTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                (DATASETS / "backtest_trades.jsonl").write_text(
                    "\n".join(json.dumps(t, default=str) for t in combined_trades), encoding="utf-8"
                )
                _write_report(summary)
                print(
                    f"  {combo}: {a['trades']} trades, {a['win_rate_pct']}% win, "
                    f"net Rs {a['net_pnl']:,} (gross {a['gross_pnl']:,}, "
                    f"friction {a['friction_paid']:,}), maxDD Rs {a['max_drawdown']:,}",
                    flush=True,
                )

    _write_report(summary)
    print(f"\nDone. research/reports/backtest_all_styles.md + "
          f"research/datasets/backtest_trades.jsonl ({len(combined_trades)} trades)")


if __name__ == "__main__":
    main()
