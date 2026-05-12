from __future__ import annotations

import json
import re
from typing import Any

from trading_ai_engine.secrets_bridge import env_or_local

_FNO_LIKE = re.compile(r"^(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX)", re.I)

# Yahoo headline indices → OpenAlgo (index root, exchange). Extend via TRADING_AI_OPENALGO_INDEX_MAP.
_DEFAULT_INDEX: dict[str, tuple[str, str]] = {
    "^NSEI": ("NIFTY", "NSE_INDEX"),
    "^NSEBANK": ("BANKNIFTY", "NSE_INDEX"),
    "^CNXIT": ("CNXIT", "NSE_INDEX"),
    "^CNXINFRA": ("CNXINFRA", "NSE_INDEX"),
    "^CNXMETAL": ("CNXMETAL", "NSE_INDEX"),
    "^CNXAUTO": ("CNXAUTO", "NSE_INDEX"),
    "^CNXENERGY": ("CNXENERGY", "NSE_INDEX"),
    "^CNXFMCG": ("CNXFMCG", "NSE_INDEX"),
    "^CNXPHARMA": ("CNXPHARMA", "NSE_INDEX"),
    "^CNXREALTY": ("CNXREALTY", "NSE_INDEX"),
    "^CNXMEDIA": ("CNXMEDIA", "NSE_INDEX"),
    "^CNXPSUBANK": ("CNXPSUBANK", "NSE_INDEX"),
    "^CNXFIN": ("CNXFIN", "NSE_INDEX"),
    "^CNX200": ("CNX200", "NSE_INDEX"),
    "^CNX500": ("CNX500", "NSE_INDEX"),
    "^CNXMCAP": ("CNXMCAP", "NSE_INDEX"),
    "^INDIAVIX": ("INDIAVIX", "NSE_INDEX"),
}


def _index_overrides() -> dict[str, tuple[str, str]]:
    out = dict(_DEFAULT_INDEX)
    raw = (env_or_local("TRADING_AI_OPENALGO_INDEX_MAP") or "").strip()
    if not raw:
        return out
    try:
        blob: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(blob, dict):
        return out
    for k, v in blob.items():
        if not isinstance(k, str) or not k.startswith("^"):
            continue
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            sym, ex = str(v[0]), str(v[1])
            if sym and ex:
                out[k.upper()] = (sym.upper(), ex.upper())
    return out


def yahoo_to_openalgo(symbol: str, *, instrument_type: str) -> tuple[str, str]:
    """
    Map canonical NSE Yahoo symbol to (openalgo_symbol, exchange).

    - Cash: ``RELIANCE.NS`` → (``RELIANCE``, ``NSE``)
    - Index: ``^NSEI`` → (``NIFTY``, ``NSE_INDEX``) when known
    - F&O: underlying-style or contract symbol → (``SYMBOL``, ``NFO``) best-effort
    """
    s = (symbol or "").strip()
    u = s.upper()
    if u.endswith(".NS"):
        base = s[: s.rfind(".")].strip().upper()
        if not base:
            raise ValueError("Empty symbol before .NS")
        if instrument_type == "fno" or "CE" in u or "PE" in u or _FNO_LIKE.match(base):
            return base, "NFO"
        return base, "NSE"
    if u.startswith("^"):
        mp = _index_overrides()
        hit = mp.get(u) or mp.get(u.upper())
        if hit:
            return hit
        raise ValueError(
            f"No OpenAlgo index mapping for {symbol!r}. "
            "Add it to TRADING_AI_OPENALGO_INDEX_MAP as JSON "
            '(e.g. {"^MYINDEX":["ROOT","NSE_INDEX"]}) or use paper (local) mode.'
        )
    raise ValueError(f"Unsupported symbol for OpenAlgo routing: {symbol!r}")
