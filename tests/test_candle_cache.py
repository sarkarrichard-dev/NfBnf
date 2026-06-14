from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from index_ai.candle_cache import ingest_frame, list_cached_days, load_cached_range


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("index_ai.candle_cache.CACHE_ROOT", tmp_path / "candles")
    monkeypatch.setattr("index_ai.candle_cache.META_PATH", tmp_path / "candles" / "meta.json")


def _frame() -> pd.DataFrame:
    ist = ZoneInfo("Asia/Kolkata")
    rows = []
    base = datetime(2026, 6, 3, 9, 15, tzinfo=ist)
    for i in range(10):
        rows.append(
            {
                "datetime": base + timedelta(minutes=5 * i),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 100,
            }
        )
    base2 = datetime(2026, 6, 4, 9, 15, tzinfo=ist)
    for i in range(10):
        rows.append(
            {
                "datetime": base2 + timedelta(minutes=5 * i),
                "open": 110 + i,
                "high": 111 + i,
                "low": 109 + i,
                "close": 110.5 + i,
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


def test_ingest_and_load_cached_range() -> None:
    counts = ingest_frame("NIFTY", "5", _frame())
    assert "2026-06-03" in counts
    assert "2026-06-04" in counts
    assert list_cached_days("NIFTY", "5") == ["2026-06-03", "2026-06-04"]
    loaded = load_cached_range("NIFTY", "5")
    assert len(loaded) == 20


def test_cache_status_flags_legacy_5m(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai.candle_cache import cache_status

    monkeypatch.setattr("index_ai.candle_cache.CACHE_ROOT", tmp_path / "candles")
    monkeypatch.setattr("index_ai.candle_cache.META_PATH", tmp_path / "candles" / "meta.json")
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "1")
    ingest_frame("NIFTY", "5", _frame())
    status = cache_status()
    assert status["active_interval_minutes"] == "1"
    assert status["instruments"]["NIFTY"]["needs_sync"] is True
    assert status["legacy"]["NIFTY"]["cached_days"] == 2
