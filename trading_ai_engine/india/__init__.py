"""India market utilities (IST, NSE-style session hints, index catalog)."""

from trading_ai_engine.india.constituents import get_indices_catalog
from trading_ai_engine.india.ist_time import IST, format_utc_iso_in_ist, ist_date_window_to_utc_bounds
from trading_ai_engine.india.market_clock import market_snapshot
from trading_ai_engine.india.nse_yahoo import (
    is_nse_yahoo_symbol,
    normalize_nse_yahoo_symbol,
    require_nifty_option_underlying,
)

__all__ = [
    "IST",
    "format_utc_iso_in_ist",
    "get_indices_catalog",
    "is_nse_yahoo_symbol",
    "ist_date_window_to_utc_bounds",
    "market_snapshot",
    "normalize_nse_yahoo_symbol",
    "require_nifty_option_underlying",
]
