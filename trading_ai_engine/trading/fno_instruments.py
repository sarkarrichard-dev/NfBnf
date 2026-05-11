from __future__ import annotations

import json
import os
import re
from typing import Any

# NSE index / liquid names → lot size (contracts). Override via TRADING_AI_FNO_LOT_MAP JSON.
_DEFAULT_LOTS: dict[str, int] = {
    "^nsei": 50,
    "nifty": 50,
    "nifty 50": 50,
    "nsebank": 15,
    "^nsebank": 15,
    "banknifty": 15,
    "bank nifty": 15,
    "sensex": 10,
    "finnifty": 25,
    "midcpnifty": 50,
}


def _lot_map() -> dict[str, int]:
    raw = (os.environ.get("TRADING_AI_FNO_LOT_MAP") or "").strip()
    m = {k: int(v) for k, v in _DEFAULT_LOTS.items()}
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if isinstance(v, (int, float)) and str(k).strip():
                        m[str(k).strip().lower()] = int(v)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return m


def classify_instrument(symbol: str, *, market_focus: str = "") -> str:
    s = (symbol or "").strip().upper()
    if re.search(r"\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}", s):
        if "FUT" in s or "CE" in s or "PE" in s:
            return "fno"
    if "FUT" in s or s.endswith("CE") or s.endswith("PE") or " CE" in s or " PE" in s:
        return "fno"
    if (market_focus or "").strip().lower() == "derivatives_intraday":
        return "fno"
    return "equity"


def _underlying_guess(symbol: str) -> str:
    s = symbol.strip().upper()
    if "^NSEI" in s or s == "^NSEI":
        return "nifty"
    if "^NSEBANK" in s or "BANKNIFTY" in s:
        return "banknifty"
    if "NIFTY" in s and "BANK" not in s:
        return "nifty"
    if "BANKNIFTY" in s:
        return "banknifty"
    if "SENSEX" in s:
        return "sensex"
    if "FINNIFTY" in s:
        return "finnifty"
    if "MIDCPNIFTY" in s:
        return "midcpnifty"
    return symbol.split(".")[0].lower() if symbol else "unknown"


def lot_size_for_symbol(symbol: str, *, market_focus: str = "") -> int:
    """Units per contract (NSE index F&O style). Equity spot uses 1."""
    if classify_instrument(symbol, market_focus=market_focus) == "equity":
        return 1
    m = _lot_map()
    u = _underlying_guess(symbol)
    if u in m:
        return max(1, int(m[u]))
    key = symbol.strip().lower()
    if key in m:
        return max(1, int(m[key]))
    for k, v in m.items():
        if k in key.replace("^", "").lower():
            return max(1, int(v))
    return 50


def fno_contract_context(symbol: str, *, market_focus: str = "") -> dict[str, Any]:
    kind = classify_instrument(symbol, market_focus=market_focus)
    ls = lot_size_for_symbol(symbol, market_focus=market_focus) if kind == "fno" else 1
    return {
        "instrument_type": "fno" if kind == "fno" else "equity",
        "lot_size": int(ls),
        "underlying_guess": _underlying_guess(symbol),
    }
