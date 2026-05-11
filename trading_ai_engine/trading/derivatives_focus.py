"""
Design-center helpers: index / equity **options** and **futures** with **intraday** bars.

Yahoo Finance is used for research-style OHLC; execution-grade ticks and NSE/BSE contract
chains are expected from a broker feed (e.g. Dhan) when wired.
"""

from __future__ import annotations

import os
from typing import Any

FOCUS_BALANCED = "balanced"
FOCUS_DERIVATIVES_INTRADAY = "derivatives_intraday"

WORKSTATION_MARKET_FOCUS_BLURB = (
    "Primary design target: Indian **F&O** (options + index futures) and **intraday** session "
    "decisions. For Yahoo index charts use ``^NSEI`` (Nifty 50 spot); ``NIFTY.NS`` is often missing. "
    "Option-chain heatmaps augment AIML context. Paper sizing uses spot-style units until lot/margin models exist."
)

DEFAULT_INTRADAY_INTERVAL = "5m"


def env_market_focus() -> str:
    raw = (os.environ.get("TRADING_AI_MARKET_FOCUS") or FOCUS_BALANCED).strip().lower()
    if raw == FOCUS_DERIVATIVES_INTRADAY:
        return FOCUS_DERIVATIVES_INTRADAY
    return FOCUS_BALANCED


def suggested_period_for_interval(interval: str) -> str:
    """Conservative Yahoo windows (see yfinance limits; tune as needed)."""
    iv = interval.strip().lower()
    if iv in ("1m", "2m"):
        return "7d"
    if iv in ("5m", "15m"):
        return "60d"
    if iv in ("30m", "60m", "90m", "1h"):
        return "730d"
    return "60d"


def resolve_brain_ohlc(
    *,
    market_focus: str,
    period: str,
    interval: str,
    include_yahoo_deep: bool,
) -> tuple[str, str]:
    """
    Return ``(ohlc_period, ohlc_interval)`` for ``market_yfinance.history``.

    * ``derivatives_intraday`` — intraday chart for the same symbol (index / future / ADR proxy).
    * ``balanced`` — legacy default: daily 5y when Yahoo deep is on, else ``period`` at 1d.
    """
    focus = (market_focus or FOCUS_BALANCED).strip().lower()
    if focus == FOCUS_DERIVATIVES_INTRADAY:
        intv = (interval or DEFAULT_INTRADAY_INTERVAL).strip().lower()
        if intv in ("", "1d"):
            intv = DEFAULT_INTRADAY_INTERVAL
        per = (period or "").strip() or suggested_period_for_interval(intv)
        return per, intv

    intv = (interval or "1d").strip().lower()
    if intv == "1d":
        if include_yahoo_deep:
            return "5y", "1d"
        return (period or "1y").strip() or "1y", "1d"
    per = (period or "").strip() or suggested_period_for_interval(intv)
    return per, intv


def readiness_market_focus_block() -> dict[str, Any]:
    return {
        "default_from_env": env_market_focus(),
        "allowed_values": [FOCUS_BALANCED, FOCUS_DERIVATIVES_INTRADAY],
        "blurb": WORKSTATION_MARKET_FOCUS_BLURB,
        "api": "POST /api/brain/analyze with market_focus + interval (e.g. 5m) + period (e.g. 60d)",
    }
