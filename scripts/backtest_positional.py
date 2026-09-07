"""
Positional (multi-day) futures backtest — does the signal pay more per toll?

    python -m scripts.backtest_positional
    python -m scripts.backtest_positional --instruments NIFTY --sessions 500
    python -m scripts.backtest_positional --param max_hold_days=5 --param stop_atr=2.5

Friction is charged per trade, so holding days instead of hours is the one lever
that changes the edge/cost ratio without needing a better signal. Spot-replay
(fair for futures), stops that gap through fill at the next open. Writes
research/positional/.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

OUT = Path("research/positional")


def _num(v: str):
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _agg(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate_pct": 0, "net_rupees": 0, "gross_rupees": 0,
                "friction_rupees": 0, "profit_factor": None, "max_drawdown": 0,
                "avg_hold_days": 0, "net_by_year": {}, "by_reason": {}}
    nets = [t["net_rupees"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    eq = peak = maxdd = 0.0
    for n in nets:
        eq += n
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    by_year: dict[str, float] = defaultdict(float)
    by_reason: dict[str, int] = defaultdict(int)
    for t in trades:
        by_year[str(t.get("entry_date"))[:4]] += t["net_rupees"]
        by_reason[t.get("reason", "?")] += 1
    return {
        "trades": len(nets),
        "win_rate_pct": round(100 * len(wins) / len(nets), 1),
        "net_rupees": round(sum(nets)),
        "gross_rupees": round(sum(t["gross_rupees"] for t in trades)),
        "friction_rupees": round(sum(t["friction_rupees"] for t in trades)),
        "gross_per_trade": round(st.mean(t["gross_rupees"] for t in trades)),
        "friction_per_trade": round(st.mean(t["friction_rupees"] for t in trades)),
        "expectancy": round(sum(nets) / len(nets)),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_drawdown": round(maxdd),
        "avg_hold_days": round(st.mean(t["hold_days"] for t in trades), 1),
        "net_by_year": {k: round(v) for k, v in sorted(by_year.items()) if k and k != "None"},
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="*", default=["NIFTY", "BANKNIFTY", "SENSEX"])
    ap.add_argument("--sessions", type=int, default=0)
    ap.add_argument("--param", action="append", default=[], metavar="FIELD=VAL")
    args = ap.parse_args()

    from index_ai.candle_cache import load_cached_range
    from index_ai.strategies.futures.positional import run

    overrides = {k: _num(v) for k, v in (p.split("=", 1) for p in args.param if "=" in p)}
    OUT.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    for key in [i.upper() for i in args.instruments]:
        bars = load_cached_range(key, "15")
        if bars.empty:
            print(f"{key}: no cache — skip")
            continue
        trades = run(key, bars, sessions=args.sessions, **overrides)
        a = _agg(trades)
        a["span"] = f"{trades[0]['entry_date']} .. {trades[-1]['exit_date']}" if trades else "—"
        summary[key] = a
        (OUT / f"{key}.json").write_text(
            json.dumps({"summary": a, "trades": trades}, indent=2), encoding="utf-8")
        print(
            f"  {key} ({a['span']}): {a['trades']} trades, avg hold {a['avg_hold_days']}d, "
            f"{a['win_rate_pct']}% win, PF {a['profit_factor']}, "
            f"net Rs {a['net_rupees']:,} (gross/trade {a['gross_per_trade']:,} vs "
            f"friction/trade {a['friction_per_trade']:,}), maxDD Rs {a['max_drawdown']:,}",
            flush=True)

    lines = ["# Positional (multi-day) futures backtest\n",
             "_Spot-replay. Stops that gap through fill at the NEXT OPEN, not the stop level. "
             "Positional futures need overnight margin (~1.5-2L/lot) — a positive result here is "
             "a signal finding, not a deployable strategy at 1.4L._\n",
             "| Instrument | Span | Trades | Hold(d) | Win% | PF | Gross/trade | Friction/trade | Net ₹ | Max DD ₹ |",
             "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for k, a in summary.items():
        lines.append(
            f"| {k} | {a['span']} | {a['trades']} | {a['avg_hold_days']} | {a['win_rate_pct']} | "
            f"{a['profit_factor']} | {a['gross_per_trade']:,} | {a['friction_per_trade']:,} | "
            f"{a['net_rupees']:,} | {a['max_drawdown']:,} |")
    for k, a in summary.items():
        if a.get("net_by_year"):
            lines.append(f"\n*{k} by year* — " + "  ".join(f"{y}: ₹{v:,}" for y, v in a["net_by_year"].items()))
        if a.get("by_reason"):
            lines.append(f"*{k} exits* — " + "  ".join(f"{r}: {n}" for r, n in a["by_reason"].items()))
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nWrote research/positional/report.md")


if __name__ == "__main__":
    main()
