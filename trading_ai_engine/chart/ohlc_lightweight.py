from __future__ import annotations

from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")


def _bar_time_unix(dt: Any) -> int:
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        ts = ts.tz_localize(_IST)
    else:
        ts = ts.tz_convert(_IST)
    return int(ts.timestamp())


def ohlc_to_lightweight_chart(df: pd.DataFrame, *, max_bars: int = 720) -> dict[str, Any]:
    """
    Serialize normalized OHLCV (``date, open, high, low, close, volume``) for
    `TradingView Lightweight Charts` (``time`` as Unix seconds, IST wall-clock).
    """
    lim = max(32, min(int(max_bars), 2000))
    if df is None or len(df) == 0:
        return {"bars": [], "count": 0, "schema": "lightweight_v4"}
    tail = df.tail(lim)
    bars: list[dict[str, Any]] = []
    for row in tail.to_dict(orient="records"):
        try:
            t = _bar_time_unix(row["date"])
            o = float(row["open"])
            h = float(row["high"])
            lo = float(row["low"])
            c = float(row["close"])
            v = float(row["volume"]) if pd.notna(row.get("volume")) else 0.0
        except (TypeError, ValueError, KeyError):
            continue
        if not all(map(lambda x: x == x and abs(x) < 1e100, (o, h, lo, c))):  # noqa: PLR2004
            continue
        bars.append({"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": max(0.0, v)})
    return {"bars": bars, "count": len(bars), "schema": "lightweight_v4"}
