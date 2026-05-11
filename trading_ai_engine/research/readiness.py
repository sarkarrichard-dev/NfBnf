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

PAPER_READY = [
    "Risk-sized paper orders from each brain decision",
    "Paper trade journal stored in SQLite",
    "Evolution ledger from feedback and paper execution",
    "Hard live-order block unless live gates are satisfied",
]


def bot_readiness_snapshot() -> dict[str, Any]:
    return {
        "live_trading_enabled": False,
        "mode": "research_only",
        "reason": (
            "The project can analyze, backtest, adapt from feedback, and paper trade. Live "
            "autonomous broker orders remain disabled until compliance, risk, broker, and "
            "paper-trading gates are satisfied."
        ),
        "paper_trading_ready": True,
        "paper_capabilities": PAPER_READY,
        "required_for_live": REQUIRED_FOR_LIVE,
    }
