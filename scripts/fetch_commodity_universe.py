"""Resolve the near-month Dhan security id / lot units / expiry for each MCX
commodity in `commodities.instruments.COMMODITIES` from the Dhan scrip master.

    python -m scripts.fetch_commodity_universe

Writes `memory/commodity_universe.json`. Re-run monthly (the front contract
rolls); the commodity roots and specs themselves are stable code constants.
"""

from __future__ import annotations

import io
import json

import httpx
import pandas as pd

from commodities.instruments import COMMODITIES, UNIVERSE_META_PATH

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def fetch() -> dict[str, dict]:
    raw = httpx.get(SCRIP_MASTER_URL, timeout=90, follow_redirects=True).text
    df = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
    fut = df[(df["SEM_EXM_EXCH_ID"] == "MCX") & (df["SEM_INSTRUMENT_NAME"] == "FUTCOM")].copy()
    fut["_root"] = fut["SEM_TRADING_SYMBOL"].str.upper().str.replace(r"[-\s].*$", "", regex=True)
    fut["_exp"] = pd.to_datetime(fut["SEM_EXPIRY_DATE"], errors="coerce")
    today = pd.Timestamp.now().normalize()
    out: dict[str, dict] = {}
    for spec in COMMODITIES:
        rows = fut[(fut["_root"] == spec.root) & (fut["_exp"] >= today)].sort_values("_exp")
        if rows.empty:
            print(f"  !! {spec.key}: no live FUTCOM contract for root {spec.root}")
            continue
        r = rows.iloc[0]  # front month
        out[spec.key] = {
            "security_id": int(r["SEM_SMST_SECURITY_ID"]),
            "lot_units": int(float(r["SEM_LOT_UNITS"])),
            "expiry": r["_exp"].date().isoformat(),
            "trading_symbol": r["SEM_TRADING_SYMBOL"],
        }
        print(f"  {spec.key:12s} secid={out[spec.key]['security_id']:>8} "
              f"lot={out[spec.key]['lot_units']} expiry={out[spec.key]['expiry']}")
    return out


if __name__ == "__main__":
    meta = fetch()
    UNIVERSE_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {UNIVERSE_META_PATH} ({len(meta)} contracts)")
