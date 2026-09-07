"""
A/B the Ichimoku cloud-reentry exit on the BUY lane against cached spot candles.

Runs the bar-by-bar replay twice per instrument (cloud_exit off vs on) and prints
buy-lane metrics side by side. Uses whatever is in memory/candles/<KEY>_<iv>m/.

    python -m scripts.backtest_cloud_exit_ab                 # NIFTY + BANKNIFTY, 1m
    python -m scripts.backtest_cloud_exit_ab SENSEX --iv 5   # one instrument, 5m

This is a slow, offline validation tool — expect a few minutes per instrument.
"""

from __future__ import annotations

import argparse
import os
import statistics as st

import pandas as pd


def _metrics(trades: list[dict]) -> dict[str, float]:
    buys = [t for t in trades if t["action"] in ("BUY_CALL", "BUY_PUT")]
    if not buys:
        return {k: 0 for k in _COLS}
    pnls = [float(t["proxy_pnl_rupees"]) for t in buys]
    gross = [float(t.get("gross_proxy_pnl_rupees") or t["proxy_pnl_rupees"]) for t in buys]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    holds: list[float] = []
    for t in buys:
        try:
            holds.append(
                (pd.Timestamp(t["exit_time"]) - pd.Timestamp(t["entry_time"])).total_seconds() / 60
            )
        except Exception:
            pass
    return {
        "trades": len(buys),
        "win_rate": round(100 * len(wins) / len(buys), 1),
        "net_pnl": round(sum(pnls)),
        "gross_pnl": round(sum(gross)),
        "avg_win": round(st.mean(wins)) if wins else 0,
        "avg_loss": round(st.mean(losses)) if losses else 0,
        "worst": round(min(pnls)),
        "avg_hold_min": round(st.mean(holds)) if holds else 0,
        "cloud_exits": sum(1 for t in buys if t.get("exit_action") == "CLOUD_EXIT"),
    }


_COLS = [
    "trades",
    "win_rate",
    "net_pnl",
    "gross_pnl",
    "avg_win",
    "avg_loss",
    "worst",
    "avg_hold_min",
    "cloud_exits",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("instruments", nargs="*", default=["NIFTY", "BANKNIFTY"])
    parser.add_argument("--iv", default="1", help="candle interval minutes (default 1)")
    args = parser.parse_args()

    os.environ.setdefault("STRATEGY_STYLE", "BUY")  # isolate the buy lane

    from index_ai.backtest import _replay_candles
    from index_ai.candle_cache import load_cached_range
    from index_ai.config import freeze_env, settings
    from index_ai.instruments import get_instrument
    from index_ai.strategies.strategy_params import reload_strategy_params

    reload_strategy_params()
    app = settings()
    freeze_env()

    for key in args.instruments or ["NIFTY", "BANKNIFTY"]:
        candles = load_cached_range(key, args.iv)
        if candles.empty:
            print(f"\n{key}: no cached {args.iv}m candles — skipping.")
            continue
        inst = get_instrument(key)
        base, _, sess = _replay_candles(
            candles, instrument=inst, app_settings=app, pnl_mode="option_proxy", cloud_exit=False
        )
        cloud, _, _ = _replay_candles(
            candles, instrument=inst, app_settings=app, pnl_mode="option_proxy", cloud_exit=True
        )
        span = f"{candles['datetime'].min().date()} .. {candles['datetime'].max().date()}"
        print(f"\n=== {key}  ({max(0, len(sess) - 1)} sessions, {span}) ===")
        b, c = _metrics(base), _metrics(cloud)
        print(f"{'metric':<14}{'baseline':>12}{'+cloud_exit':>14}")
        for col in _COLS:
            print(f"{col:<14}{b[col]:>12}{c[col]:>14}")


if __name__ == "__main__":
    main()
