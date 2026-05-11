from __future__ import annotations

from typing import Any

import pandas as pd


def extra_strategy_metrics(ohlc: pd.DataFrame) -> dict[str, Any]:
    """
    Lightweight multi-style proxies on the **primary** symbol's OHLC (same bar size as the brain).

    These are feature columns for fusion / LLM context — not separate backtests.
    """
    df = ohlc.dropna(subset=["close"]).copy()
    if df.empty or len(df) < 25:
        return {}

    c = df["close"].astype(float)
    sma20 = c.rolling(20, min_periods=10).mean()
    sma50 = c.rolling(50, min_periods=20).mean()
    last20 = c.iloc[-21:-1]
    cur = float(c.iloc[-1])
    z_lb = min(20, len(last20))
    past = c.iloc[-(z_lb + 1) : -1]
    mr_z = 0.0
    if len(past) >= 3:
        m = float(past.mean())
        st = float(past.std(ddof=0)) or 1e-12
        mr_z = (cur - m) / st

    s20 = float(sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) else cur
    s50 = float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else cur
    trend_ratio = (s20 - s50) / (abs(s50) + 1e-12)
    ret1 = float(c.pct_change().iloc[-1]) if len(c) > 1 else 0.0
    vol20 = float(c.pct_change().rolling(20, min_periods=5).std().iloc[-1] or 0.0)

    return {
        "strat_trend_ma20_ma50_ratio": round(trend_ratio, 6),
        "strat_mean_reversion_z20": round(float(mr_z), 6),
        "strat_last_bar_return": round(ret1, 6),
        "strat_realized_vol_20bar": round(vol20, 6),
    }
