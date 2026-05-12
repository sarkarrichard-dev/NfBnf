from __future__ import annotations

import os
import re
from typing import Any

from trading_ai_engine.india.nse_yahoo import normalize_nse_yahoo_symbol


def trading_agents_import_ok() -> bool:
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401

        return True
    except ImportError:
        return False


def _ticker_for_propagate(symbol: str) -> str:
    """
    TradingAgents examples use bare tickers (``NVDA``). Strip Yahoo NSE suffix for yfinance-style symbols.
    """
    s = symbol.strip().upper()
    if s.endswith(".NS"):
        return s[:-3] or s
    if s == "^NSEI":
        return "NIFTY"
    if s == "^NSEBANK":
        return "BANKNIFTY"
    if s.startswith("^"):
        return s[1:] or s
    return s


def run_trading_agents_propagate(
    *,
    symbol: str,
    trade_date: str,
    debug: bool = False,
) -> dict[str, Any]:
    """
    Run `TradingAgentsGraph.propagate` (see upstream `main.py`).
    Requires ``pip install -e ".[tradingagents]"`` and provider keys in ``.env`` per
    https://github.com/TauricResearch/TradingAgents
    """
    if not trading_agents_import_ok():
        return {
            "status": "unavailable",
            "detail": (
                "TradingAgents is not installed. Install optional extra: pip install -e \".[tradingagents]\" "
                "(see pyproject.toml)."
            ),
        }
    sym = normalize_nse_yahoo_symbol(symbol.strip())
    ticker = _ticker_for_propagate(sym)
    if not re.match(r"^[A-Z0-9._-]{1,32}$", ticker):
        return {"status": "error", "detail": f"Unsupported ticker after normalize: {ticker!r}"}

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    ta = TradingAgentsGraph(debug=bool(debug), config=config)
    try:
        state, decision = ta.propagate(ticker, trade_date.strip())
    except Exception as e:  # noqa: BLE001 — surface upstream failures to operator
        return {"status": "error", "ticker": ticker, "trade_date": trade_date, "detail": str(e)}

    def _short(obj: Any, limit: int = 8000) -> Any:
        if obj is None:
            return None
        s = str(obj)
        return s if len(s) <= limit else s[:limit] + "…"

    out: dict[str, Any] = {
        "status": "ok",
        "ticker": ticker,
        "yahoo_symbol": sym,
        "trade_date": trade_date.strip(),
        "decision": _short(decision),
    }
    if isinstance(state, dict):
        out["state_summary"] = {k: _short(v, 1200) for k, v in list(state.items())[:40]}
    else:
        out["state_type"] = type(state).__name__
    return out


def trading_agents_env_hint() -> dict[str, Any]:
    return {
        "installed": trading_agents_import_ok(),
        "repo": "https://github.com/TauricResearch/TradingAgents",
        "env_prefix": "TRADINGAGENTS_*",
        "timeout_seconds": int(os.environ.get("TRADING_AI_TRADINGAGENTS_TIMEOUT_S", "120") or 120),
    }
