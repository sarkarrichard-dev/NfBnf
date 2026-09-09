"""The stock-futures research universe.

The ~20 most liquid NSE single-stock futures. This is a **code constant** touched
only by the backtest (`scripts/backtest_stock_futures.py`) — it is never read by
the live system and never edited by a customer.

The *derived* data — Dhan security ids and current F&O lot sizes — is fetched
from the Dhan scrip master by `scripts/fetch_stock_universe.py` and cached to
`memory/stock_universe.json`; `load_universe_meta()` reads it back.
"""

from __future__ import annotations

import json
from pathlib import Path

from index_ai.config import MEMORY_DIR

STOCK_FUTURES_UNIVERSE: tuple[str, ...] = (
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "TCS",
    "SBIN",
    "AXISBANK",
    "BHARTIARTL",
    "LT",
    "ITC",
    "KOTAKBANK",
    "HINDUNILVR",
    "MARUTI",
    "TATASTEEL",  # (was TATAMOTORS — demerged 2025 into TMPV/TMCV, history broken)
    "BAJFINANCE",
    "HCLTECH",
    "SUNPHARMA",
    "TITAN",
    "ADANIENT",
    "WIPRO",
)

UNIVERSE_META_PATH = MEMORY_DIR / "stock_universe.json"


def load_universe_meta(path: str | Path = UNIVERSE_META_PATH) -> dict[str, dict[str, int]]:
    """``{symbol: {"security_id": int, "lot_size": int}}`` from the fetch cache."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":  # self-check
    assert len(STOCK_FUTURES_UNIVERSE) == len(set(STOCK_FUTURES_UNIVERSE)) == 20
    if UNIVERSE_META_PATH.is_file():
        meta = load_universe_meta()
        missing = [s for s in STOCK_FUTURES_UNIVERSE if s not in meta]
        print(f"stock_universe: {len(meta)} resolved, missing {missing or 'none'}")
    else:
        print("stock_universe: 20 names; run scripts.fetch_stock_universe to resolve ids/lots")
