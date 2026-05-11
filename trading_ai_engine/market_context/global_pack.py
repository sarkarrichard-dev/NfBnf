from __future__ import annotations

import os
import re
from typing import Any

import pandas as pd

from trading_ai_engine.market_yfinance import history

_DEFAULT_GLOBAL = "^GSPC,SPY,EURUSD=X,USDINR=X,CL=F"


def _metric_key(symbol: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", symbol.strip()).strip("_").lower()
    return f"global_{s}"


def fetch_global_context_snapshot(
    *,
    period: str = "10d",
    interval: str = "1d",
) -> dict[str, Any]:
    """
    Pull a compact Yahoo snapshot for configured global / cross-asset symbols.

    ``TRADING_AI_GLOBAL_CONTEXT_SYMBOLS`` — comma-separated Yahoo tickers (default US index,
    FX, crude, INR). Fails soft per symbol so one bad ticker does not break Analyze.
    """
    raw = (os.environ.get("TRADING_AI_GLOBAL_CONTEXT_SYMBOLS") or _DEFAULT_GLOBAL).strip()
    symbols = [s.strip() for s in raw.split(",") if s.strip()][:12]
    lines: list[str] = []
    metrics: dict[str, Any] = {}
    per_sym: dict[str, Any] = {}
    errors: list[str] = []

    for sym in symbols:
        key = _metric_key(sym)
        try:
            df = history(sym, period=period, interval=interval)
            if df is None or df.empty or "close" not in df.columns:
                errors.append(f"{sym}: no rows")
                continue
            c = df["close"].astype(float)
            last = float(c.iloc[-1])
            prev = float(c.iloc[-2]) if len(c) > 1 else last
            ret_1 = (last / prev - 1.0) if prev else 0.0
            ret_5d = (last / float(c.iloc[0]) - 1.0) if len(c) > 1 else 0.0
            metrics[f"{key}_close"] = round(last, 6)
            metrics[f"{key}_ret_1bar"] = round(ret_1, 6)
            metrics[f"{key}_ret_window"] = round(ret_5d, 6)
            metrics[f"{key}_bars"] = int(len(df))
            per_sym[sym] = {"close": last, "ret_1bar": ret_1, "bars": len(df)}
            lines.append(
                f"{sym}: close={last:.4f} ret_1bar={ret_1:+.4%} ret_vs_window_start={ret_5d:+.4%} bars={len(df)}"
            )
        except Exception as e:
            errors.append(f"{sym}: {e}")

    digest = (
        "Global / cross-asset Yahoo snapshot (research context, not execution feed):\n"
        + ("\n".join(lines) if lines else "(no symbols succeeded)")
    )
    if errors:
        digest += "\n\nPartial errors:\n" + "\n".join(f"- {x}" for x in errors[:8])

    return {
        "digest": digest[:8000],
        "metrics": metrics,
        "symbols_ok": list(per_sym.keys()),
        "errors": errors,
        "period": period,
        "interval": interval,
    }
