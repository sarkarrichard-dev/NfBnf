"""Persist Dhan intraday candles locally for multi-day backtests."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from index_ai.config import MEMORY_DIR, candle_interval_minutes
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.instruments import configured_index_keys, get_instrument

_log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
CACHE_ROOT = MEMORY_DIR / "candles"
META_PATH = CACHE_ROOT / "meta.json"
DHAN_PULL_MAX_CALENDAR_DAYS = 5


def _cache_dir(instrument_key: str, interval: str) -> Path:
    key = instrument_key.strip().upper()
    return CACHE_ROOT / f"{key}_{interval}m"


def _day_path(instrument_key: str, interval: str, day: date) -> Path:
    return _cache_dir(instrument_key, interval) / f"{day.isoformat()}.csv"


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["datetime"] = pd.to_datetime(work["datetime"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["datetime", "open", "high", "low", "close"])
    work = work.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return work.reset_index(drop=True)


def save_session_day(instrument_key: str, interval: str, day: date, frame: pd.DataFrame) -> int:
    """Upsert one session day to CSV; returns rows stored."""
    if frame.empty:
        return 0
    day_frame = _normalize_frame(frame)
    day_frame = day_frame[pd.to_datetime(day_frame["datetime"]).dt.date == day].copy()
    if day_frame.empty:
        return 0
    path = _day_path(instrument_key, interval, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    day_frame.to_csv(path, index=False)
    return len(day_frame)


def load_cached_range(
    instrument_key: str,
    interval: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> pd.DataFrame:
    """Load merged candles from local cache (empty frame if none)."""
    root = _cache_dir(instrument_key, interval)
    if not root.exists():
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

    frames: list[pd.DataFrame] = []
    for path in sorted(root.glob("*.csv")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        part = pd.read_csv(path, parse_dates=["datetime"])
        frames.append(part)

    if not frames:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    return _normalize_frame(pd.concat(frames, ignore_index=True))


def list_cached_days(instrument_key: str, interval: str) -> list[str]:
    root = _cache_dir(instrument_key, interval)
    if not root.exists():
        return []
    days: list[str] = []
    for path in sorted(root.glob("*.csv")):
        try:
            days.append(path.stem)
        except ValueError:
            continue
    return days


def _split_by_session(frame: pd.DataFrame) -> dict[date, pd.DataFrame]:
    work = _normalize_frame(frame)
    work["session"] = pd.to_datetime(work["datetime"]).dt.date
    out: dict[date, pd.DataFrame] = {}
    for day in sorted(work["session"].unique()):
        part = work[work["session"] == day].drop(columns=["session"]).reset_index(drop=True)
        if not part.empty:
            out[day] = part
    return out


def ingest_frame(instrument_key: str, interval: str, frame: pd.DataFrame) -> dict[str, int]:
    """Merge candle rows into per-day cache files."""
    counts: dict[str, int] = {}
    for day, part in _split_by_session(frame).items():
        counts[day.isoformat()] = save_session_day(instrument_key, interval, day, part)
    return counts


def sync_from_dhan(
    client: DhanClient,
    instrument_key: str,
    *,
    interval: str | None = None,
    lookback_days: int = DHAN_PULL_MAX_CALENDAR_DAYS,
) -> dict[str, Any]:
    """Pull latest Dhan intraday window and upsert into local cache."""
    iv = str(interval or candle_interval_minutes())
    key = instrument_key.strip().upper()
    instrument = get_instrument(key)
    if instrument.underlying_security_id is None:
        raise ValueError(f"{key} security id is not configured.")

    lookback_days = max(1, min(int(lookback_days), DHAN_PULL_MAX_CALENDAR_DAYS))
    now = datetime.now(IST)
    start = now - timedelta(days=lookback_days)
    raw = client.intraday_history(
        instrument,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=iv,
    )
    frame = chart_response_to_frame(raw)
    if frame.empty:
        return {"instrument": key, "interval": iv, "rows_fetched": 0, "days_updated": {}}

    counts = ingest_frame(key, iv, frame)
    _touch_meta(key, iv, counts)
    return {
        "instrument": key,
        "interval": iv,
        "rows_fetched": len(frame),
        "days_updated": counts,
        "cached_days": list_cached_days(key, iv),
    }


def sync_all_configured(client: DhanClient, *, interval: str | None = None) -> list[dict[str, Any]]:
    iv = str(interval or candle_interval_minutes())
    results: list[dict[str, Any]] = []
    for key in configured_index_keys():
        try:
            results.append(sync_from_dhan(client, key, interval=iv))
        except Exception as exc:
            _log.warning("Candle cache sync failed for %s: %s", key, exc)
            results.append({"instrument": key, "error": str(exc)})
    return results


def _touch_meta(instrument_key: str, interval: str, counts: dict[str, int]) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {}
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    key = f"{instrument_key}_{interval}m"
    meta[key] = {
        "last_sync_ist": datetime.now(IST).isoformat(timespec="seconds"),
        "days_updated": counts,
        "total_days": len(list_cached_days(instrument_key, interval)),
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def cache_status() -> dict[str, Any]:
    """Summary for dashboard / API."""
    active_iv = candle_interval_minutes()
    out: dict[str, Any] = {
        "active_interval_minutes": active_iv,
        "instruments": {},
        "legacy": {},
        "meta": {},
    }
    if META_PATH.exists():
        try:
            out["meta"] = json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            out["meta"] = {}
    for key in configured_index_keys():
        days = list_cached_days(key, active_iv)
        legacy_days = list_cached_days(key, "5") if active_iv != "5" else []
        out["instruments"][key] = {
            "interval_minutes": active_iv,
            "cached_days": len(days),
            "first_day": days[0] if days else None,
            "last_day": days[-1] if days else None,
            "needs_sync": len(days) == 0,
        }
        if legacy_days and active_iv != "5":
            out["legacy"][key] = {
                "interval_minutes": "5",
                "cached_days": len(legacy_days),
                "note": "Legacy 5m cache — live algo uses 1m; run Sync cache for 1m data.",
            }
    out["needs_sync"] = any(v.get("needs_sync") for v in out["instruments"].values())
    return out


def ensure_active_interval_cache(client: DhanClient) -> list[dict[str, Any]]:
    """Pull 1m (active interval) cache when empty but Dhan is available."""
    status = cache_status()
    if not status.get("needs_sync"):
        return []
    iv = candle_interval_minutes()
    results: list[dict[str, Any]] = []
    for key, info in status.get("instruments", {}).items():
        if not info.get("needs_sync"):
            continue
        try:
            results.append(sync_from_dhan(client, key, interval=iv))
        except Exception as exc:
            _log.warning("Initial %sm cache sync failed for %s: %s", iv, key, exc)
    return results


def fetch_backtest_candles(
    client: DhanClient | None,
    instrument_key: str,
    *,
    interval: str | None = None,
    lookback_days: int = 5,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> tuple[pd.DataFrame, str]:
    """
    Load candles for backtest: cache (+ optional Dhan refresh), then trim range.

    Returns (frame, data_source_label).
    """
    key = instrument_key.strip().upper()
    iv = str(interval or candle_interval_minutes())
    lookback_days = max(1, min(int(lookback_days), 365))
    to_day = datetime.now(IST).date()
    from_day = to_day - timedelta(days=lookback_days)

    if refresh_cache and client is not None:
        sync_from_dhan(client, key, interval=iv, lookback_days=DHAN_PULL_MAX_CALENDAR_DAYS)

    cached = load_cached_range(key, iv, from_date=from_day, to_date=to_day) if use_cache else pd.DataFrame()

    latest_end = None
    if not cached.empty:
        latest_end = pd.to_datetime(cached["datetime"]).max().date()

    need_dhan = client is not None and (
        cached.empty or latest_end is None or (to_day - latest_end).days >= 1 or refresh_cache
    )
    if need_dhan and client is not None:
        try:
            pulled = sync_from_dhan(client, key, interval=iv, lookback_days=DHAN_PULL_MAX_CALENDAR_DAYS)
            if pulled.get("rows_fetched"):
                cached = load_cached_range(key, iv, from_date=from_day, to_date=to_day)
        except Exception as exc:
            if cached.empty:
                raise RuntimeError(f"Dhan fetch failed and cache empty: {exc}") from exc
            _log.warning("Dhan refresh failed; using cache only: %s", exc)

    if cached.empty:
        if client is None:
            raise RuntimeError("No cached candles and Dhan client unavailable.")
        now = datetime.now(IST)
        start = now - timedelta(days=min(lookback_days, DHAN_PULL_MAX_CALENDAR_DAYS))
        instrument = get_instrument(key)
        raw = client.intraday_history(
            instrument,
            from_date=start.strftime("%Y-%m-%d 09:15:00"),
            to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            interval=iv,
        )
        frame = chart_response_to_frame(raw)
        if frame.empty:
            raise RuntimeError("Dhan returned no intraday candles.")
        ingest_frame(key, iv, frame)
        cached = load_cached_range(key, iv, from_date=from_day, to_date=to_day)
        source = "dhan_intraday"
    else:
        source = "cache+dhan" if need_dhan else "cache"

    if cached.empty:
        raise RuntimeError(
            f"No candles for {key} between {from_day} and {to_day}. "
            "Run candle cache sync while market data is available."
        )
    return cached, source
