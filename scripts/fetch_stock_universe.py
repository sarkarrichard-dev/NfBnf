"""Resolve Dhan security ids + current F&O lot sizes for the stock-futures
research universe from the Dhan scrip master.

    python -m scripts.fetch_stock_universe

Writes ``memory/stock_universe.json``:

    {"RELIANCE": {"security_id": 2885, "lot_size": 500}, ...}

Re-run after an F&O lot-size revision (quarterly-ish). Security ids are stable.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterable

import httpx
import pandas as pd

from index_ai.strategies.futures.stock_universe import (
    STOCK_FUTURES_UNIVERSE,
    UNIVERSE_META_PATH,
)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def fetch_universe(symbols: Iterable[str]) -> dict[str, dict[str, int]]:
    raw = httpx.get(SCRIP_MASTER_URL, timeout=60, follow_redirects=True).text
    df = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
    want = {s.upper() for s in symbols}
    sym = df["SEM_TRADING_SYMBOL"].str.upper()

    # NSE cash equity: trading symbol is the bare name (series EQ).
    eq = df[(df["SEM_EXM_EXCH_ID"] == "NSE")
            & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")
            & sym.isin(want)]

    # NSE stock futures: trading symbol is "<SYM>-<Mon><Year>-FUT"; the lot size
    # is identical across the live expiries, so take the first per base symbol.
    fut = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "FUTSTK")].copy()
    fut["_base"] = fut["SEM_TRADING_SYMBOL"].str.upper().str.split("-").str[0]
    fut = fut[fut["_base"].isin(want)]
    lot_by_sym = (
        fut.groupby("_base")["SEM_LOT_UNITS"].first().astype(float).astype(int).to_dict()
    )

    out: dict[str, dict[str, int]] = {}
    for _, r in eq.iterrows():
        s = str(r["SEM_TRADING_SYMBOL"]).upper()
        out[s] = {
            "security_id": int(float(r["SEM_SMST_SECURITY_ID"])),
            "lot_size": int(lot_by_sym.get(s, 0)),
        }

    missing = sorted(want - out.keys())
    no_lot = sorted(s for s, v in out.items() if not v["lot_size"])
    if missing:
        print(f"WARNING unresolved (no NSE EQ row): {missing}")
    if no_lot:
        print(f"WARNING no FUTSTK lot size: {no_lot}")
    return out


def main() -> None:
    data = fetch_universe(STOCK_FUTURES_UNIVERSE)
    UNIVERSE_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_META_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {UNIVERSE_META_PATH} — {len(data)}/{len(STOCK_FUTURES_UNIVERSE)} resolved")


if __name__ == "__main__":
    main()
