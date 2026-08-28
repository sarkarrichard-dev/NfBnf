"""
Resample the cached 1-minute candles into 5m / 15m caches — no API calls.

    python -m scripts.resample_candles                 # 1m -> 5m, all instruments
    python -m scripts.resample_candles --to 5 15       # also 15m
    python -m scripts.resample_candles --instruments NIFTY

Writes memory/candles/<KEY>_<N>m/<date>.csv, one file per session, overwriting.
Backtests then read that interval via load_cached_range(key, "5").
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path("memory/candles")
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _resample_session(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    df = df.sort_values("datetime")
    out = (
        df.set_index("datetime")
        .resample(f"{minutes}min", origin="start", label="left", closed="left")
        .agg(_AGG)
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", nargs="*", type=int, default=[5], help="target interval minutes")
    ap.add_argument("--instruments", nargs="*", default=None)
    args = ap.parse_args()

    keys = args.instruments or [
        p.name.rsplit("_", 1)[0]
        for p in ROOT.iterdir()
        if p.is_dir() and p.name.endswith("_1m")
    ]
    for key in sorted(set(keys)):
        src = ROOT / f"{key}_1m"
        if not src.exists():
            print(f"{key}: no 1m cache — skip")
            continue
        files = sorted(src.glob("*.csv"))
        for minutes in args.to:
            dst = ROOT / f"{key}_{minutes}m"
            dst.mkdir(parents=True, exist_ok=True)
            n = 0
            for f in files:
                day = f.stem
                df = pd.read_csv(f, parse_dates=["datetime"])
                if df.empty:
                    continue
                out = _resample_session(df, minutes)
                if not out.empty:
                    out.to_csv(dst / f"{day}.csv", index=False)
                    n += 1
            print(f"{key}: {n} sessions -> {minutes}m ({dst})")


if __name__ == "__main__":
    main()
