"""The investing section's real universe — every NSE stock, no OpenBB involved.

Dhan's own scrip master (the same CSV ``scripts/fetch_stock_universe.py``
already trusts for the 20-name futures universe) lists every NSE instrument.
F&O eligibility is NSE's own liquid-stock cut — a natural "broad but not
everything" universe for a first screener, much wider than the hand-picked
20 futures names. Widen to the full ~2,000-name EQUITY list later if the
per-symbol fundamentals fetch (``investing/openbb_bridge.py``) can keep up
with that many calls.
"""

from __future__ import annotations

import io
import json

import httpx
import pandas as pd

from index_ai.config import MEMORY_DIR

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
UNIVERSE_CACHE_PATH = MEMORY_DIR / "investing" / "nse_fo_universe.json"


def fetch_fo_universe() -> dict[str, dict[str, int]]:
    """``{symbol: {"security_id": int}}`` for every NSE stock with a live future."""
    raw = httpx.get(SCRIP_MASTER_URL, timeout=60, follow_redirects=True).text
    df = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)

    fut = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "FUTSTK")].copy()
    fut["_base"] = fut["SEM_TRADING_SYMBOL"].str.upper().str.split("-").str[0]
    fo_symbols = set(fut["_base"].unique())

    eq = df[
        (df["SEM_EXM_EXCH_ID"] == "NSE")
        & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")
        & (df["SEM_TRADING_SYMBOL"].str.upper().isin(fo_symbols))
    ]
    return {
        str(r["SEM_TRADING_SYMBOL"]).upper(): {"security_id": int(float(r["SEM_SMST_SECURITY_ID"]))}
        for _, r in eq.iterrows()
    }


def load_or_fetch_universe(*, force: bool = False) -> dict[str, dict[str, int]]:
    if not force and UNIVERSE_CACHE_PATH.is_file():
        return json.loads(UNIVERSE_CACHE_PATH.read_text(encoding="utf-8"))
    data = fetch_fo_universe()
    UNIVERSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


if __name__ == "__main__":  # self-check — hits the real Dhan scrip master
    d = fetch_fo_universe()
    assert len(d) > 100, f"expected 100+ F&O-eligible NSE stocks, got {len(d)}"
    assert "RELIANCE" in d, d.keys()
    print(f"investing.universe: {len(d)} F&O-eligible NSE stocks")
