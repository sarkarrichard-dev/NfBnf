from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from index_ai.config import MEMORY_DIR
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.instruments import IndexInstrument


DATASET_DIR = MEMORY_DIR / "datasets"


def _save_dataset(frame: pd.DataFrame, path: Path) -> dict[str, Any]:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        frame = pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    clean = frame.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    clean.to_csv(path, index=False)
    return {
        "file": str(path),
        "rows": len(clean),
        "from": str(clean.iloc[0]["datetime"]) if not clean.empty else None,
        "to": str(clean.iloc[-1]["datetime"]) if not clean.empty else None,
    }


def download_daily_dataset(
    client: DhanClient,
    instrument: IndexInstrument,
    *,
    years: int = 7,
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    start = now - timedelta(days=365 * max(1, years))
    data = client.historical_daily(
        instrument,
        from_date=start.isoformat(),
        to_date=now.isoformat(),
    )
    frame = chart_response_to_frame(data)
    path = DATASET_DIR / f"{instrument.key}_daily_{years}y.csv"
    result = _save_dataset(frame, path)
    result["kind"] = "daily"
    result["instrument"] = instrument.key
    return result


def download_intraday_dataset(
    client: DhanClient,
    instrument: IndexInstrument,
    *,
    years: int = 5,
    interval: str = "5",
) -> dict[str, Any]:
    clamped_years = max(1, min(5, years))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    cursor = now - timedelta(days=365 * clamped_years)
    frames: list[pd.DataFrame] = []
    chunks = 0
    while cursor < now:
        chunk_end = min(cursor + timedelta(days=89), now)
        data = client.intraday_history(
            instrument,
            from_date=cursor.strftime("%Y-%m-%d 09:15:00"),
            to_date=chunk_end.strftime("%Y-%m-%d 15:30:00"),
            interval=interval,
        )
        frame = chart_response_to_frame(data)
        if not frame.empty:
            frames.append(frame)
        chunks += 1
        cursor = chunk_end + timedelta(days=1)

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    path = DATASET_DIR / f"{instrument.key}_intraday_{interval}m_{clamped_years}y.csv"
    result = _save_dataset(merged, path)
    result["kind"] = "intraday"
    result["instrument"] = instrument.key
    result["chunks"] = chunks
    result["requested_years"] = years
    result["downloaded_years"] = clamped_years
    return result
