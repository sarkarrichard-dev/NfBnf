"""crypto/candle_cache.py -- 2026-09-18: crypto never had persistent candle
caching (unlike India's index_ai.candle_cache), so every backtest re-fetched
identical history from Delta's live API every time. Covers the isolation
(tmp_path, no real cache dir touched) and the one real bug this surfaced:
a partial top-up fetch must merge into an existing day's file, not overwrite
it and silently drop already-cached hours."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from crypto import candle_cache


@pytest.fixture(autouse=True)
def _isolated_cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)


def _recent_day() -> str:
    """A day inside the 10-day window whenever the suite runs (a fixed date
    silently aged out of it on 2026-09-21 and broke these tests)."""
    return (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d 00:00")


def _hourly_frame(start: str, n: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"datetime": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    )


def test_save_days_splits_by_utc_calendar_day():
    frame = _hourly_frame("2026-09-01 22:00", 6)  # spans Sep 1 into Sep 2
    n = candle_cache.save_days("BTCUSD", "1h", frame)
    assert n == 6
    files = sorted(f.name for f in candle_cache._cache_dir("BTCUSD", "1h").glob("*.csv"))
    assert files == ["2026-09-01.csv", "2026-09-02.csv"]


def test_save_days_merges_a_partial_overlap_without_losing_existing_hours():
    """The bug found 2026-09-18: cached_candles' gap-top-up fetch starts at a
    precise now-minus-N-hours point, often partway through a day whose
    earlier hours are already cached. save_days must merge, not overwrite."""
    full_day = _hourly_frame("2026-09-05 00:00", 24)
    candle_cache.save_days("BTCUSD", "1h", full_day)

    # a small top-up that only covers the last 3 hours of the same day
    partial = _hourly_frame("2026-09-05 21:00", 3)
    candle_cache.save_days("BTCUSD", "1h", partial)

    back = candle_cache.load_cached_range(
        "BTCUSD", "1h", from_date=date(2026, 9, 5), to_date=date(2026, 9, 5)
    )
    assert len(back) == 24  # all 24 hours still present, not truncated to 3


def test_save_days_updates_on_a_real_value_change():
    day1 = _hourly_frame("2026-09-05 00:00", 24)
    candle_cache.save_days("BTCUSD", "1h", day1)

    revised = _hourly_frame("2026-09-05 23:00", 1)
    revised["close"] = 999.0
    candle_cache.save_days("BTCUSD", "1h", revised)

    back = candle_cache.load_cached_range(
        "BTCUSD", "1h", from_date=date(2026, 9, 5), to_date=date(2026, 9, 5)
    )
    assert len(back) == 24
    assert float(back.iloc[-1]["close"]) == 999.0


def test_load_cached_range_filters_by_date():
    candle_cache.save_days("BTCUSD", "1h", _hourly_frame("2026-09-01 00:00", 24))
    candle_cache.save_days("BTCUSD", "1h", _hourly_frame("2026-09-02 00:00", 24))
    only_first = candle_cache.load_cached_range(
        "BTCUSD", "1h", from_date=date(2026, 9, 1), to_date=date(2026, 9, 1)
    )
    assert len(only_first) == 24
    assert only_first["datetime"].dt.date.eq(date(2026, 9, 1)).all()


def test_cached_candles_only_tops_up_the_gap_on_a_warm_cache(monkeypatch):
    """A cold cache asks Delta for the full window; a warm cache should ask
    for a small gap, not re-fetch everything -- the whole point of this."""
    calls: list[float] = []
    now = datetime.now(timezone.utc)

    def fake_candles(symbol, resolution, *, days, client=None):
        calls.append(days)
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:00")
        hours = int(days * 24)
        return _hourly_frame(start, hours)

    monkeypatch.setattr("crypto.delta.market_data.candles", fake_candles)

    candle_cache.cached_candles("BTCUSD", "1h", days=10)
    candle_cache.cached_candles("BTCUSD", "1h", days=10)

    assert len(calls) == 2
    assert calls[0] == 10  # cold cache: full window
    assert calls[1] < calls[0]  # warm cache: only the recent gap


def test_cached_candles_falls_back_to_cache_when_live_fetch_fails(monkeypatch):
    candle_cache.save_days("BTCUSD", "1h", _hourly_frame(_recent_day(), 24))

    def failing_candles(*a, **k):
        raise RuntimeError("Delta unreachable")

    monkeypatch.setattr("crypto.delta.market_data.candles", failing_candles)

    result = candle_cache.cached_candles("BTCUSD", "1h", days=10)
    assert len(result) == 24  # cache-only, doesn't blow up or return empty


def test_cached_candles_refresh_false_is_cache_only(monkeypatch):
    candle_cache.save_days("BTCUSD", "1h", _hourly_frame(_recent_day(), 24))

    def unexpected_call(*a, **k):
        raise AssertionError("refresh=False must never call the live API")

    monkeypatch.setattr("crypto.delta.market_data.candles", unexpected_call)

    result = candle_cache.cached_candles("BTCUSD", "1h", days=10, refresh=False)
    assert len(result) == 24
