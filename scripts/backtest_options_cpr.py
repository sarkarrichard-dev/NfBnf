"""
Backtest the CPR + EMA option-buying strategy over the candle cache.

    python -m scripts.backtest_options_cpr                        # NIFTY BANKNIFTY SENSEX, full history
    python -m scripts.backtest_options_cpr --instruments NIFTY --sessions 250
    python -m scripts.backtest_options_cpr --param strike_selection=OTM1 --param iv=0.14
    python -m scripts.backtest_options_cpr --no-15m                # drop the 15m EMA-alignment gate

Spot-replay with a Black-Scholes premium proxy (no historical option chain) —
signal stats are meaningful, rupee P&L is indicative. Writes research/options_cpr/.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

OUT = Path("research/options_cpr")


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
          "net_by_year": {}, "ce": 0, "pe": 0, "by_reason": {}}


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
    by_reason: dict[str, int] = defaultdict(int)
    for t in trades:
        by_year[str(t.get("session"))[:4]] += t["net_rupees"]
        by_reason[t.get("reason", "?")] += 1
    return {
        "trades": len(nets),
        "win_rate_pct": round(100 * len(wins) / len(nets), 1),
        "net_rupees": round(sum(nets)),
        "gross_rupees": round(sum(t["gross_rupees"] for t in trades)),
        "friction_rupees": round(sum(t["friction_rupees"] for t in trades)),
        "avg_win": round(st.mean(wins)) if wins else 0,
        "avg_loss": round(st.mean(losses)) if losses else 0,
        "expectancy": round(sum(nets) / len(nets)),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_drawdown": round(maxdd),
        "net_by_year": {k: round(v) for k, v in sorted(by_year.items()) if k and k != "None"},
        "ce": sum(1 for t in trades if t["side"] == "CE"),
        "pe": sum(1 for t in trades if t["side"] == "PE"),
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="*", default=["NIFTY", "BANKNIFTY", "SENSEX"])
    ap.add_argument("--sessions", type=int, default=0)
    ap.add_argument("--param", action="append", default=[], metavar="FIELD=VAL")
    ap.add_argument("--no-15m", action="store_true", help="drop the 15m EMA-alignment gate")
    args = ap.parse_args()

    from index_ai.candle_cache import load_cached_range
    from index_ai.strategies.options_cpr.backtest import run
    from index_ai.strategies.options_cpr.config import config_for, with_overrides

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
        trades = run(key, b5, b15, cfg=cfg, sessions=args.sessions,
                     require_15m_alignment=not args.no_15m)
        a = _agg(trades)
        a["span"] = f"{trades[0]['session']} .. {trades[-1]['session']}" if trades else "—"
        summary[key] = a
        (OUT / f"{key}.json").write_text(
            json.dumps({"summary": a, "trades": trades}, indent=2), encoding="utf-8"
        )
        print(
            f"  {key} ({a['span']}): {a['trades']} trades ({a['ce']}CE/{a['pe']}PE), "
            f"{a['win_rate_pct']}% win, PF {a['profit_factor']}, "
            f"net Rs {a['net_rupees']:,} (gross {a['gross_rupees']:,}, friction {a['friction_rupees']:,}), "
            f"maxDD Rs {a['max_drawdown']:,}",
            flush=True,
        )

    lines = [
        "# CPR + EMA option-buying backtest\n",
        "_Spot-replay with a Black-Scholes premium proxy (no historical option chain). "
        "Signal stats are meaningful; rupee P&L is indicative — calibrate vs the paper journal. "
        "Per-trade loss is capped at 5% of **utilised** capital (premium x lot), per spec Section 4 — "
        "this makes every stop <= 5% of premium, which is tight; flip `max_loss_pct_of_utilized_capital` "
        "or the basis if that is not intended._\n",
        "| Instrument | Span | Trades | CE/PE | Win% | PF | Net ₹ | Gross ₹ | Friction ₹ | Expectancy ₹ | Max DD ₹ |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for k, a in summary.items():
        lines.append(
            f"| {k} | {a['span']} | {a['trades']} | {a['ce']}/{a['pe']} | {a['win_rate_pct']} | "
            f"{a['profit_factor']} | {a['net_rupees']:,} | {a['gross_rupees']:,} | "
            f"{a['friction_rupees']:,} | {a['expectancy']:,} | {a['max_drawdown']:,} |"
        )
    for k, a in summary.items():
        if a.get("net_by_year"):
            lines.append(f"\n*{k} by year* — " + "  ".join(f"{y}: ₹{v:,}" for y, v in a["net_by_year"].items()))
        if a.get("by_reason"):
            lines.append(f"*{k} exits* — " + "  ".join(f"{r}: {n}" for r, n in a["by_reason"].items()))
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nWrote research/options_cpr/report.md")


if __name__ == "__main__":
    main()
