from __future__ import annotations

from typing import Any

from trading_ai_engine.server.research import run_symbol_backtest

SignalModeStr = str


def sweep_backtest_grid(
    symbol: str,
    *,
    period: str = "5y",
    horizons: tuple[int, ...] = (3, 5, 8),
    cost_bps_list: tuple[float, ...] = (4.0, 8.0, 12.0),
    signal_modes: tuple[SignalModeStr, ...] = ("structural", "trend_ma", "mean_reversion_z"),
) -> dict[str, Any]:
    """
    Small coarse grid over backtest hyperparameters (research only).

    Ranks by ending_equity then profit_factor; ties broken by max_drawdown (less negative better).
    """
    sym = symbol.strip()
    rows: list[dict[str, Any]] = []
    for mode in signal_modes:
        for h in horizons:
            for c in cost_bps_list:
                try:
                    out = run_symbol_backtest(
                        sym,
                        period=period,
                        horizon_bars=h,
                        cost_bps=c,
                        signal_mode=str(mode),
                    )
                except ValueError:
                    continue
                s = out.get("summary") or {}
                rows.append(
                    {
                        "signal_mode": str(mode),
                        "horizon_bars": h,
                        "cost_bps": c,
                        "trades": s.get("trades"),
                        "ending_equity": s.get("ending_equity"),
                        "max_drawdown": s.get("max_drawdown"),
                        "profit_factor": s.get("profit_factor"),
                        "win_rate": s.get("win_rate"),
                        "status": s.get("status"),
                    }
                )

    def sort_key(r: dict[str, Any]) -> tuple[float, float, float]:
        ee = float(r.get("ending_equity") or 0.0)
        pf = float(r["profit_factor"]) if r.get("profit_factor") is not None else -1.0
        mdd = float(r.get("max_drawdown") or 0.0)
        return (ee, pf, mdd)

    ranked = sorted([r for r in rows if r.get("status") == "ok"], key=sort_key, reverse=True)
    best = ranked[0] if ranked else None
    return {
        "symbol": sym,
        "period": period,
        "grid_size": len(rows),
        "ranked": ranked[:12],
        "best": best,
        "warning": "Research-only; does not optimize live execution or slippage from a real book.",
    }
