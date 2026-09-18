"""Persist Delta Exchange candles locally, for backtests/research only.

Mirrors ``index_ai.candle_cache``'s pattern (one CSV per calendar day, load-
then-fetch-only-the-missing-tail) -- India already solved "don't re-download
the same history on every backtest run"; crypto never had this at all.
``crypto.delta.market_data.candles`` always hits Delta's live API for the
full requested window, so every backtest re-downloads identical historical
data from scratch. This is deliberately NOT wired into the live scanning
path (``crypto/lanes.py``) -- that needs the freshest incomplete candle every
tick, which a day-granularity cache isn't built for; this only speeds up
backtests and research scripts that ask for the same history repeatedly.

No IST session window here (unlike the India cache) -- crypto trades 24/7,
so a "day" is just a plain UTC calendar day, no market-hours filtering.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from crypto.config import CRYPTO_MEMORY
from crypto.delta.client import DeltaClient

_log = logging.getLogger(__name__)

CACHE_ROOT = CRYPTO_MEMORY / "crypto_candles"


def _cache_dir(symbol: str, resolution: str) -> Path:
    return CACHE_ROOT / f"{symbol.strip().upper()}_{resolution}"


def _day_path(symbol: str, resolution: str, day: date) -> Path:
    return _cache_dir(symbol, resolution) / f"{day.isoformat()}.csv"


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["datetime"] = pd.to_datetime(work["datetime"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["datetime", "open", "high", "low", "close"])
    return (
        work.sort_values("datetime")
        .drop_duplicates(subset=["datetime"], keep="last")
        .reset_index(drop=True)
    )


def save_days(symbol: str, resolution: str, frame: pd.DataFrame) -> int:
    """Split a frame by UTC calendar day and merge each day into its CSV.

    A "top up the recent tail" fetch (``cached_candles``'s ``gap_days``) uses
    a precise now-minus-N-hours window, not a day-aligned one -- it often
    starts partway through a day whose earlier hours are already cached. A
    blind overwrite here would delete those already-cached hours; merging
    with whatever's already on disk (dedup on datetime, keep the new value
    on a clash) is what makes a partial re-fetch safe to upsert."""
    if frame.empty:
        return 0
    work = _normalize(frame)
    written = 0
    for day, day_frame in work.groupby(work["datetime"].dt.date):
        path = _day_path(symbol, resolution, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            existing = pd.read_csv(path, parse_dates=["datetime"])
            day_frame = _normalize(pd.concat([existing, day_frame], ignore_index=True))
        day_frame.to_csv(path, index=False)
        written += len(day_frame)
    return written


def load_cached_range(
    symbol: str, resolution: str, *, from_date: date | None = None, to_date: date | None = None
) -> pd.DataFrame:
    root = _cache_dir(symbol, resolution)
    if not root.exists():
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frames = []
    for path in sorted(root.glob("*.csv")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        frames.append(pd.read_csv(path, parse_dates=["datetime"]))
    if not frames:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    return _normalize(pd.concat(frames, ignore_index=True))


def cached_candles(
    symbol: str,
    resolution: str,
    *,
    days: float,
    client: DeltaClient | None = None,
    refresh: bool = True,
) -> pd.DataFrame:
    """Backtest/research candle fetch: local cache first, then top up only
    the missing recent days from Delta's live API (not the whole window
    every time). ``refresh=False`` returns cache-only, for fully offline runs."""
    from crypto.delta import market_data

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    cached = load_cached_range(symbol, resolution, from_date=start, to_date=end)

    if not refresh:
        return cached

    latest_cached = cached["datetime"].max().date() if not cached.empty else None
    # always re-pull from the latest fully-cached day onward (today's file is
    # provisional, and the boundary day may have been incomplete last time)
    gap_days = days if latest_cached is None else max(1.0, (end - latest_cached).days + 1)
    try:
        fresh = market_data.candles(symbol, resolution, days=gap_days, client=client)
    except Exception:
        _log.warning(
            "crypto candle cache: live refresh failed for %s, using cache only",
            symbol,
            exc_info=True,
        )
        return cached
    if not fresh.empty:
        save_days(symbol, resolution, fresh)
        cached = load_cached_range(symbol, resolution, from_date=start, to_date=end)
    return cached


if __name__ == "__main__":  # self-check -- no network, synthetic frame only
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    orig_root = CACHE_ROOT
    globals()["CACHE_ROOT"] = tmp  # redirect for the self-check only

    idx = pd.date_range("2026-09-01", periods=48, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {"datetime": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    )
    n = save_days("BTCUSD", "1h", frame)
    assert n == 48, n
    files = list(_cache_dir("BTCUSD", "1h").glob("*.csv"))
    assert len(files) == 2, files  # spans two UTC calendar days

    back = load_cached_range("BTCUSD", "1h", from_date=date(2026, 9, 1), to_date=date(2026, 9, 2))
    assert len(back) == 48, len(back)
    assert list(back.columns[:5]) == ["datetime", "open", "high", "low", "close"]

    narrow = load_cached_range("BTCUSD", "1h", from_date=date(2026, 9, 2), to_date=date(2026, 9, 2))
    assert len(narrow) == 24, len(narrow)

    globals()["CACHE_ROOT"] = orig_root
    shutil.rmtree(tmp, ignore_errors=True)
    print("crypto.candle_cache self-check ok")
