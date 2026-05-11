"""NSE-focused Yahoo Finance tickers (``.NS`` equities and flagship ``^`` NSE indices)."""

from __future__ import annotations

from typing import Final

# Yahoo Finance codes commonly used for NSE headline indices in this repo.
_NSE_INDEX_YAHOO: Final[frozenset[str]] = frozenset(
    {
        "^NSEI",
        "^NSEBANK",
        "^CNXIT",
        "^CNXINFRA",
        "^CNXMETAL",
        "^CNXAUTO",
        "^CNXENERGY",
        "^CNXFMCG",
        "^CNXPHARMA",
        "^CNXREALTY",
        "^CNXMEDIA",
        "^CNXPSUBANK",
        "^CNXFIN",
        "^CNX200",
        "^CNX500",
        "^CNXMCAP",
        "^INDIAVIX",
    }
)


def normalize_nse_yahoo_symbol(symbol: str) -> str:
    """
    Return a canonical Yahoo symbol for NSE cash or supported NSE indices.

    - Equities: must end with ``.NS`` (Yahoo's NSE suffix), e.g. ``RELIANCE.NS``.
    - Indices: must be one of the supported ``^...`` codes (e.g. ``^NSEI``).

    BSE ``.BO`` and non-Indian tickers are rejected.
    """
    s = (symbol or "").strip()
    if not s:
        raise ValueError("Symbol is required for NSE mode.")
    u = s.upper()
    if u.endswith(".BO"):
        raise ValueError("BSE (.BO) symbols are disabled in NSE-only mode. Use a .NS ticker or ^NSEI.")
    if u in _NSE_INDEX_YAHOO:
        return u
    if u.endswith(".NS") and len(u) > 3:
        i = s.rfind(".")
        return s[: i + 1] + "NS"
    raise ValueError(
        "Only NSE Yahoo tickers are allowed: use a suffix .NS (e.g. RELIANCE.NS, TCS.NS) "
        f"or a supported NSE index such as ^NSEI. Received: {symbol!r}"
    )


def require_nifty_option_underlying(underlying: str) -> str:
    """Local option files and Dhan stubs in this repo target NIFTY (NSE) only."""
    u = (underlying or "nifty").strip().lower()
    if u != "nifty":
        raise ValueError(
            "NSE-only: option-chain heatmaps are wired for NIFTY. Use underlying=nifty (not sensex / other roots)."
        )
    return "nifty"


def is_nse_yahoo_symbol(symbol: str) -> bool:
    try:
        normalize_nse_yahoo_symbol(symbol)
        return True
    except ValueError:
        return False
