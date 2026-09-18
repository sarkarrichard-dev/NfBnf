"""Equity-curve charts for the TA-Lib/backtrader strategies
(scripts/backtest_talib_backtrader.py) -- added 2026-09-18 to actually use
matplotlib rather than leave it installed and untouched.

Re-runs both strategies on both markets (same methodology, same real charge
functions as the runner script) and plots cumulative net P&L over the trade
sequence, one line per instrument plus a bold total line, so the *shape* of
each result (steady bleed vs a few big losses vs choppy) is visible at a
glance, not just the final number.

    python -m scripts.plot_talib_backtest --out /path/to/dir
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless -- this runs from a script, not a GUI session
import matplotlib.pyplot as plt

from scripts.backtest_talib_backtrader import (
    CRYPTO_SYMBOLS,
    INDIA_INSTRUMENTS,
    STRATEGIES,
    Trade,
    backtest_crypto,
    backtest_india,
)


def _equity_by_instrument(trades: list[Trade]) -> dict[str, tuple[list[int], list[float]]]:
    """{instrument: (trade_index, cumulative_net)} -- each instrument's own
    trades in their own chronological order."""
    by_inst: dict[str, list[Trade]] = {}
    for t in trades:
        by_inst.setdefault(t.instrument, []).append(t)
    out: dict[str, tuple[list[int], list[float]]] = {}
    for inst, ts in by_inst.items():
        ts.sort(key=lambda t: t.entry_dt)
        xs, ys, running = [], [], 0.0
        for i, t in enumerate(ts, start=1):
            running += t.net
            xs.append(i)
            ys.append(running)
        out[inst] = (xs, ys)
    return out


def _total_curve(trades: list[Trade]) -> tuple[list, list[float]]:
    ts = sorted(trades, key=lambda t: t.entry_dt)
    xs, ys, running = [], [], 0.0
    for t in ts:
        running += t.net
        xs.append(t.entry_dt)
        ys.append(running)
    return xs, ys


def _plot(trades: list[Trade], title: str, ylabel: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    by_inst = _equity_by_instrument(trades)
    for inst, (xs, ys) in sorted(by_inst.items()):
        ax.plot(xs, ys, label=inst, alpha=0.55, linewidth=1.2)

    # bold total line, plotted against overall trade sequence for a clean
    # single "how did this strategy do over the whole test" read
    total_xs, total_ys = _total_curve(trades)
    ax.plot(range(1, len(total_ys) + 1), total_ys, label="TOTAL", color="black", linewidth=2.2)

    ax.axhline(0, color="grey", linewidth=0.8, linestyle="--")
    ax.set_title(title)
    ax.set_xlabel("trade #")
    ax.set_ylabel(ylabel)
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".")
    ap.add_argument("--india-days", type=int, default=730)
    ap.add_argument("--crypto-days", type=float, default=120.0)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for strat_name in STRATEGIES:
        print(f"=== {strat_name} ===")
        india_trades = backtest_india(strat_name, args.india_days)
        _plot(
            india_trades,
            f"{strat_name} -- India ({', '.join(INDIA_INSTRUMENTS)}, {args.india_days}d, 15m)",
            "cumulative net (Rs)",
            out_dir / f"{strat_name}_india_equity.png",
        )

        crypto_trades = backtest_crypto(strat_name, args.crypto_days)
        _plot(
            crypto_trades,
            f"{strat_name} -- Crypto ({', '.join(CRYPTO_SYMBOLS)}, {args.crypto_days:g}d, 1h)",
            "cumulative net (USD)",
            out_dir / f"{strat_name}_crypto_equity.png",
        )


if __name__ == "__main__":
    main()
