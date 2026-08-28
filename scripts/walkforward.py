"""
Walk-forward parameter evaluation — the only honest way to "tune" this system.

For each rolling split of the cached sessions:
  1. grid-search a small parameter set on the TRAIN window,
  2. lock in the best combo,
  3. score it once on the untouched TEST window.
Only the aggregated TEST (out-of-sample) result is reported. If OOS P&L is not
clearly positive and stable across splits, the parameters have no edge — no
amount of in-sample tuning changes that.

    python -m scripts.walkforward --instrument BANKNIFTY --train 20 --test 10

Grid is deliberately coarse (a big grid overfits the search itself). Widen it
in the GRID dict below only once you have a long history (scripts/pull_history).
"""

from __future__ import annotations

import argparse
import itertools
import os
import statistics as st

# name -> env var -> candidate values
GRID: dict[str, tuple[str, list[str]]] = {
    "ema_fast": ("EMA_FAST_PERIOD", ["8", "13"]),
    "ema_slow": ("EMA_SLOW_PERIOD", ["20", "34"]),
    "cpr_narrow": ("CPR_NARROW_WIDTH_PCT", ["0.30", "0.40"]),
    "giveback": ("PROFIT_TRAIL_GIVEBACK_PCT", ["0.25", "0.40"]),
}


def _run(candles, instrument, app, cloud_exit=False):
    from index_ai.backtest import _replay_candles

    trades, _, _ = _replay_candles(
        candles, instrument=instrument, app_settings=app, pnl_mode="option_proxy", cloud_exit=cloud_exit
    )
    pnls = [float(t["proxy_pnl_rupees"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trades": len(pnls),
        "net": round(sum(pnls)),
        "win_rate": round(100 * wins / len(pnls), 1) if pnls else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="BANKNIFTY")
    ap.add_argument("--iv", default="1")
    ap.add_argument("--train", type=int, default=20, help="sessions per train window")
    ap.add_argument("--test", type=int, default=10, help="sessions per test window")
    args = ap.parse_args()

    os.environ.setdefault("STRATEGY_STYLE", "AUTO")

    from index_ai.backtest import _sessions
    from index_ai.candle_cache import load_cached_range
    from index_ai.config import settings
    from index_ai.instruments import get_instrument
    from index_ai.strategies.strategy_params import reload_strategy_params
    import pandas as pd

    app = settings()
    inst = get_instrument(args.instrument.upper())
    all_candles = load_cached_range(args.instrument.upper(), args.iv)
    if all_candles.empty:
        raise SystemExit("No cached candles — run scripts/pull_history first.")
    sessions = _sessions(all_candles)
    days = [d for d, _ in sessions]
    win = args.train + args.test
    if len(days) < win + args.test:
        raise SystemExit(
            f"Only {len(days)} sessions cached; need > {win + args.test}. Backfill more history."
        )

    combos = list(itertools.product(*[v[1] for v in GRID.values()]))
    env_names = [v[0] for v in GRID.values()]
    print(f"{len(combos)} param combos · {args.instrument} · train {args.train} / test {args.test}\n")

    oos_results: list[dict] = []
    for split_start in range(0, len(days) - win, args.test):
        train_days = set(days[split_start : split_start + args.train])
        test_days = set(days[split_start + args.train : split_start + win])
        train_c = all_candles[pd.to_datetime(all_candles["datetime"]).dt.date.isin(train_days)]
        test_c = all_candles[pd.to_datetime(all_candles["datetime"]).dt.date.isin(test_days)]

        best, best_net = None, -1e18
        for combo in combos:
            for name, val in zip(env_names, combo):
                os.environ[name] = val
            reload_strategy_params()
            r = _run(train_c, inst, app)
            if r["net"] > best_net:
                best_net, best = r["net"], combo

        for name, val in zip(env_names, best):
            os.environ[name] = val
        reload_strategy_params()
        test_r = _run(test_c, inst, app)
        label = ",".join(f"{k}={v}" for k, v in zip(GRID.keys(), best))
        print(
            f"split {split_start:>3}  train[{min(train_days)}..{max(train_days)}] "
            f"best({label}) in-sample Rs {best_net:>8,}  ->  "
            f"OOS Rs {test_r['net']:>8,}  ({test_r['trades']} tr, {test_r['win_rate']}% win)"
        )
        oos_results.append(test_r)

    nets = [r["net"] for r in oos_results]
    print("\n=== out-of-sample summary ===")
    print(f"splits:        {len(nets)}")
    print(f"total OOS P&L: Rs {sum(nets):,}")
    print(f"mean / split:  Rs {round(st.mean(nets)):,}" if nets else "n/a")
    print(f"std / split:   Rs {round(st.pstdev(nets)):,}" if len(nets) > 1 else "n/a")
    print(f"positive splits: {sum(1 for n in nets if n > 0)} / {len(nets)}")
    print(
        "\nverdict: "
        + (
            "no demonstrated edge — OOS P&L is not reliably positive."
            if not nets or sum(nets) <= 0 or sum(1 for n in nets if n > 0) <= len(nets) / 2
            else "weakly positive OOS — worth a longer history and a paper-forward test."
        )
    )


if __name__ == "__main__":
    main()
