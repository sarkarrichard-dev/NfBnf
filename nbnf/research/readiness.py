from __future__ import annotations

from typing import Any


REQUIRED_FOR_LIVE = [
    "Audited data catalog with zero critical ingest errors",
    "Explicit supervised labels for each strategy",
    "Walk-forward backtest with positive expectancy after costs",
    "Paper trading for at least 20 market sessions",
    "Broker API integration with order tagging/compliance support",
    "Daily loss limit, max position size, and max trade count",
    "Manual kill switch tested before every live session",
    "Bad-data detector for stale quotes, missing candles, and API failures",
    "Trade journal with every signal, order, fill, and reason",
]


def bot_readiness_snapshot() -> dict[str, Any]:
    return {
        "live_trading_enabled": False,
        "mode": "research_only",
        "reason": (
            "The project can analyze and backtest, but live autonomous trading is intentionally "
            "disabled until compliance, risk, broker, and paper-trading gates are satisfied."
        ),
        "required_for_live": REQUIRED_FOR_LIVE,
    }
