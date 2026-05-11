from __future__ import annotations

from typing import Any, cast

from trading_ai_engine.market_yfinance import history
from trading_ai_engine.research.backtest import BacktestConfig, SignalMode, run_research_backtest

_VALID_MODES: frozenset[str] = frozenset({"structural", "trend_ma", "mean_reversion_z"})


def run_symbol_backtest(
    symbol: str,
    *,
    period: str = "5y",
    interval: str = "1d",
    horizon_bars: int = 5,
    cost_bps: float = 8.0,
    spread_bps: float = 0.0,
    signal_mode: str = "structural",
    fast_ma: int = 20,
    slow_ma: int = 50,
    z_lookback: int = 20,
    z_entry: float = 1.0,
    long_threshold: float = 0.25,
    short_threshold: float = -0.25,
    min_history_bars: int = 60,
) -> dict[str, Any]:
    sym = symbol.strip()
    if not sym:
        raise ValueError("symbol is required")
    mode = signal_mode.strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"signal_mode must be one of {sorted(_VALID_MODES)}")
    sm = cast(SignalMode, mode)
    if sm == "trend_ma" and int(fast_ma) >= int(slow_ma):
        raise ValueError("trend_ma requires fast_ma < slow_ma")
    ohlc = history(sym, period=period, interval=interval.strip() or "1d")
    return run_research_backtest(
        sym,
        ohlc,
        BacktestConfig(
            horizon_bars=max(1, int(horizon_bars)),
            cost_bps=float(cost_bps),
            bar_interval=interval.strip() or "1d",
            spread_bps=float(spread_bps),
            signal_mode=sm,
            fast_ma=max(2, int(fast_ma)),
            slow_ma=max(3, int(slow_ma)),
            z_lookback=max(3, int(z_lookback)),
            z_entry=max(0.1, float(z_entry)),
            long_threshold=float(long_threshold),
            short_threshold=float(short_threshold),
            min_history_bars=max(20, int(min_history_bars)),
        ),
    )
