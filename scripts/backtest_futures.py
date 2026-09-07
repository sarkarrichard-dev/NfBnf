"""
Backtest the directional index-futures strategy over the full candle cache.

    python -m scripts.backtest_futures                       # NIFTY BANKNIFTY SENSEX, full history
    python -m scripts.backtest_futures --instruments NIFTY --sessions 250
    python -m scripts.backtest_futures --param initial_stop_pts=50 --param trend_ema_slow=34

Spot-replay -> the P&L is a fair proxy for real futures (small basis), net of the
real index-futures charge schedule. Writes research/futures/.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

OUT = Path("research/futures")


def _num(v: str):
    if ":" in v:
        from datetime import time

        hh, mm = (int(x) for x in v.split(":", 1))
        return time(hh, mm)
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


_EMPTY = {"trades": 0, "win_rate_pct": 0, "net_rupees": 0, "gross_rupees": 0, "friction_rupees": 0,
          "avg_win": 0, "avg_loss": 0, "expectancy": 0, "profit_factor": None, "max_drawdown": 0,
          "net_by_year": {}, "long": 0, "short": 0}


def _agg(trades: list[dict]) -> dict:
    nets = [t["net_rupees"] for t in trades]
    if not nets:
        return dict(_EMPTY)
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    eq = peak = maxdd = 0.0
    for n in nets:
        eq += n
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    by_year: dict[str, float] = defaultdict(float)
    for t in trades:
        by_year[str(t.get("session"))[:4]] += t["net_rupees"]
    gross = sum(t["gross_rupees"] for t in trades)
    fric = sum(t["friction_rupees"] for t in trades)
    return {
        "trades": len(nets),
        "win_rate_pct": round(100 * len(wins) / len(nets), 1),
        "net_rupees": round(sum(nets)),
        "gross_rupees": round(gross),
        "friction_rupees": round(fric),
        "avg_win": round(st.mean(wins)) if wins else 0,
        "avg_loss": round(st.mean(losses)) if losses else 0,
        "expectancy": round(sum(nets) / len(nets)),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_drawdown": round(maxdd),
        "net_by_year": {k: round(v) for k, v in sorted(by_year.items()) if k and k != "None"},
        "long": sum(1 for t in trades if t["direction"] == "LONG"),
        "short": sum(1 for t in trades if t["direction"] == "SHORT"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="*", default=["NIFTY", "BANKNIFTY", "SENSEX"])
    ap.add_argument("--sessions", type=int, default=0)
    ap.add_argument("--param", action="append", default=[], metavar="FIELD=VAL")
    args = ap.parse_args()

    from index_ai.candle_cache import load_cached_range
    from index_ai.strategies.futures.backtest import run
    from index_ai.strategies.futures.config import config_for, with_overrides

    overrides = {k: _num(v) for k, v in (p.split("=", 1) for p in args.param if "=" in p)}
    OUT.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    for key in [i.upper() for i in args.instruments]:
        b5 = load_cached_range(key, "5")
        b15 = load_cached_range(key, "15")
        if b5.empty or b15.empty:
            print(f"{key}: no 5m/15m cache — skip")
            continue
        cfg = with_overrides(config_for(key), **overrides) if overrides else config_for(key)
        print(f"{key}: replaying...", flush=True)
        trades = run(key, b5, b15, cfg=cfg, sessions=args.sessions)
        a = _agg(trades)
        span = f"{trades[0]['session']} .. {trades[-1]['session']}" if trades else "—"
        a["span"] = span
        summary[key] = a
        (OUT / f"{key}.json").write_text(json.dumps({"summary": a, "trades": trades}, indent=2), encoding="utf-8")
        print(
            f"  {key} ({span}): {a['trades']} trades ({a['long']}L/{a['short']}S), "
            f"{a['win_rate_pct']}% win, PF {a['profit_factor']}, "
            f"net Rs {a['net_rupees']:,} (gross {a['gross_rupees']:,}, friction {a['friction_rupees']:,}), "
            f"maxDD Rs {a['max_drawdown']:,}",
            flush=True,
        )

    lines = ["# Directional index-futures backtest\n",
             "_Spot-replay (fair for futures, small basis). Net of the real futures charge schedule._\n",
             "| Instrument | Span | Trades | L/S | Win% | PF | Net ₹ | Gross ₹ | Friction ₹ | Expectancy ₹ | Max DD ₹ |",
             "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for k, a in summary.items():
        lines.append(
            f"| {k} | {a['span']} | {a['trades']} | {a['long']}/{a['short']} | {a['win_rate_pct']} | "
            f"{a['profit_factor']} | {a['net_rupees']:,} | {a['gross_rupees']:,} | {a['friction_rupees']:,} | "
            f"{a['expectancy']:,} | {a['max_drawdown']:,} |"
        )
    for k, a in summary.items():
        if a.get("net_by_year"):
            lines.append(f"\n*{k} by year* — " + "  ".join(f"{y}: ₹{v:,}" for y, v in a["net_by_year"].items()))
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nWrote research/futures/report.md")


if __name__ == "__main__":
    main()
