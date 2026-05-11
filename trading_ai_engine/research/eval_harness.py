from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from trading_ai_engine.server.research import run_symbol_backtest


def run_mode_comparison(
    symbol: str,
    *,
    period: str = "2y",
    interval: str = "1d",
    horizon_bars: int = 5,
    cost_bps: float = 12.0,
    spread_bps: float = 0.0,
) -> list[dict[str, Any]]:
    modes = ("structural", "trend_ma", "mean_reversion_z")
    rows: list[dict[str, Any]] = []
    for mode in modes:
        out = run_symbol_backtest(
            symbol,
            period=period,
            interval=interval,
            horizon_bars=horizon_bars,
            cost_bps=cost_bps,
            spread_bps=spread_bps,
            signal_mode=mode,
        )
        s = out.get("summary") or {}
        rows.append(
            {
                "signal_mode": mode,
                "status": s.get("status"),
                "trades": s.get("trades"),
                "ending_equity": s.get("ending_equity"),
                "max_drawdown": s.get("max_drawdown"),
                "profit_factor": s.get("profit_factor"),
                "win_rate": s.get("win_rate"),
                "assumptions": s.get("assumptions"),
            }
        )
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="Compare research backtest signal modes on one symbol.")
    p.add_argument("--symbol", default="^NSEI")
    p.add_argument("--period", default="2y")
    p.add_argument("--interval", default="1d")
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=12.0)
    p.add_argument("--spread-bps", type=float, default=0.0)
    args = p.parse_args()
    rows = run_mode_comparison(
        args.symbol,
        period=args.period,
        interval=args.interval,
        horizon_bars=args.horizon,
        cost_bps=args.cost_bps,
        spread_bps=args.spread_bps,
    )
    json.dump({"symbol": args.symbol, "rows": rows}, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
