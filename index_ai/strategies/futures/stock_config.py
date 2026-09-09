"""Per-stock ``FuturesConfig`` for the stock-futures backtest.

The index ``config.py`` sets the four absolute-point risk fields
(``initial_stop_pts`` etc.) to values tuned for NIFTY/BANKNIFTY. Those are raw
price differences — a 40-point stop is 0.17% of NIFTY but ~3% of a ₹1,400 stock,
so they cannot transfer. Everything else on ``FuturesConfig`` is scale-free
(EMA/Supertrend periods, bar counts, ``max_extension_pct``, the time-of-day
fields) and inherits the frozen-dataclass defaults unchanged.

Here the four point fields are scaled off the stock's own **daily ATR**. The K
multipliers are the point-to-ATR ratios the index configs already imply
(NIFTY: initial 45 / ATR ~200 ≈ 0.22; trail 80 ≈ 0.40; daily 110 ≈ 0.55).
"""

from __future__ import annotations

import pandas as pd

from index_ai.strategies.futures.config import FuturesConfig

# point-field / daily-ATR ratios — tune here, or sweep from the runner
K_INITIAL_STOP = 0.22
K_TRAIL_ACTIVATE = 0.22
K_TRAIL = 0.40
K_DAILY_STOP = 0.55


def daily_atr(daily_bars: pd.DataFrame) -> float:
    """Median daily true-range over the window — one regime-robust scalar. Not
    Wilder ATR(14): this is a position-sizing knob, not a signal input."""
    h, low, pc = daily_bars["high"], daily_bars["low"], daily_bars["close"].shift()
    tr = pd.concat([h - low, (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    tr = tr.dropna()
    return float(tr.median()) if len(tr) else 0.0


def stock_config(symbol: str, lot_size: int, atr_daily: float) -> FuturesConfig:
    atr = max(1.0, float(atr_daily))
    return FuturesConfig(
        key=symbol.upper(),
        lot_size=int(lot_size),
        exchange="NSE",
        initial_stop_pts=round(K_INITIAL_STOP * atr, 1),
        trail_activate_pts=round(K_TRAIL_ACTIVATE * atr, 1),
        trail_pts=round(K_TRAIL * atr, 1),
        daily_stop_pts=round(K_DAILY_STOP * atr, 1),
    )


if __name__ == "__main__":  # self-check
    import numpy as np

    n = 60
    close = pd.Series(np.linspace(1400, 1500, n))
    daily = pd.DataFrame({"high": close + 20, "low": close - 20, "close": close})
    atr = daily_atr(daily)
    assert 30 < atr < 50, atr  # ~true range of 40 with a small trend
    cfg = stock_config("RELIANCE", 500, atr)
    assert cfg.key == "RELIANCE" and cfg.lot_size == 500
    assert cfg.initial_stop_pts == round(0.22 * atr, 1)
    assert cfg.trend_ema_fast == 9 and cfg.entry_ema == 9  # scale-free defaults intact
    print(f"stock_config self-check ok — ATR {atr:.1f} -> stop {cfg.initial_stop_pts}")
