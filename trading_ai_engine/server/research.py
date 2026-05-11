from __future__ import annotations

from typing import Any

from trading_ai_engine.market_yfinance import history
from trading_ai_engine.research.backtest import BacktestConfig, run_research_backtest


def run_symbol_backtest(
    symbol: str,
    *,
    period: str = "5y",
    horizon_bars: int = 5,
    cost_bps: float = 8.0,
) -> dict[str, Any]:
    sym = symbol.strip()
    if not sym:
        raise ValueError("symbol is required")
    ohlc = history(sym, period=period, interval="1d")
    return run_research_backtest(
        sym,
        ohlc,
        BacktestConfig(
            horizon_bars=max(1, int(horizon_bars)),
            cost_bps=float(cost_bps),
        ),
    )
