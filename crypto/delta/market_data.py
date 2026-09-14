"""Delta public market data — ticker, OHLCV candles (chunked), l2 depth, resample.

All endpoints are unauthenticated. Ticker/depth get a short in-memory TTL cache
so one scan tick reading a symbol twice makes one call. Candle chunking mirrors
``reference/openalgo/broker/deltaexchange/api/data.py`` (Delta caps a response at
~2000 candles).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from crypto._util import num as _f
from crypto.delta.client import DeltaClient

logger = logging.getLogger(__name__)

# OpenAlgo interval code -> Delta resolution (identical strings, kept explicit)
RESOLUTIONS = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "1d": "1d",
    "1w": "1w",
}
# days per request, sized to stay under the ~2000-candle cap (from data.py)
_CHUNK_DAYS = {
    "1m": 1,
    "3m": 7,
    "5m": 12,
    "15m": 30,
    "30m": 60,
    "1h": 80,
    "2h": 80,
    "4h": 80,
    "6h": 80,
    "1d": 0,
    "1w": 0,
}

_TTL = 5.0
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, produce):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


def ticker(symbol: str, *, client: DeltaClient | None = None) -> dict[str, Any]:
    """Latest quote: mark_price, OHLC, oi, best_bid/ask."""
    c = client or DeltaClient()
    return _cached(f"tk:{symbol}", _TTL, lambda: c.get_public(f"/v2/tickers/{symbol}"))


def depth(symbol: str, *, client: DeltaClient | None = None) -> dict[str, Any]:
    """5-level l2 order book. Needs the product_id from the ticker."""
    c = client or DeltaClient()

    def _pull() -> dict[str, Any]:
        tk = ticker(symbol, client=c)
        pid = int(tk.get("product_id") or 0)
        book = c.get_public(f"/v2/l2orderbook/{pid}") if pid else {}
        return {
            "bids": [
                {"price": float(x.get("price", 0)), "size": float(x.get("size", 0))}
                for x in (book.get("buy") or [])[:5]
            ],
            "asks": [
                {"price": float(x.get("price", 0)), "size": float(x.get("size", 0))}
                for x in (book.get("sell") or [])[:5]
            ],
            "mark": float(tk.get("mark_price") or 0),
        }

    return _cached(f"dp:{symbol}", _TTL, _pull)


def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _fetch_candle_history(
    api_symbol: str,
    resolution: str,
    *,
    days: float,
    client: DeltaClient | None,
    require_positive: bool,
) -> pd.DataFrame:
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unsupported resolution {resolution!r}; use one of {list(RESOLUTIONS)}")
    c = client or DeltaClient()
    res = RESOLUTIONS[resolution]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    step = _CHUNK_DAYS.get(res, 30)
    windows: list[tuple[datetime, datetime]] = []
    if step == 0:
        windows.append((start, end))
    else:
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=step), end)
            windows.append((cur, nxt))
            cur = nxt

    rows: list[dict[str, Any]] = []
    for w_start, w_end in windows:
        raw = c.get_public(
            "/v2/history/candles",
            params={
                "symbol": api_symbol,
                "resolution": res,
                "start": _epoch(w_start),
                "end": _epoch(w_end),
            },
        )
        for k in raw or []:
            if isinstance(k, list) and len(k) >= 6:
                ts, o, h, low, cl, vol = k[0], k[1], k[2], k[3], k[4], k[5]
            elif isinstance(k, dict):
                ts = k.get("time", k.get("t"))
                o, h, low, cl, vol = (
                    k.get("open"),
                    k.get("high"),
                    k.get("low"),
                    k.get("close"),
                    k.get("volume"),
                )
            else:
                continue
            o, h, low, cl = _f(o), _f(h), _f(low), _f(cl)
            if ts in (None, ""):
                continue
            if require_positive and not (o > 0 and h > 0 and low > 0 and cl > 0):
                continue  # skip a malformed candle rather than poison the frame
            rows.append(
                {
                    "datetime": datetime.fromtimestamp(int(_f(ts)), tz=timezone.utc),
                    "open": o,
                    "high": h,
                    "low": low,
                    "close": cl,
                    "volume": _f(vol),
                }
            )

    if not rows:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).sort_values("datetime").drop_duplicates("datetime")
    return df.reset_index(drop=True)


def candles(
    symbol: str,
    resolution: str,
    *,
    days: float = 3.0,
    client: DeltaClient | None = None,
) -> pd.DataFrame:
    """OHLCV history as a DataFrame with a tz-aware UTC ``datetime`` column,
    ascending, de-duplicated. ``days`` is the lookback from now."""
    return _fetch_candle_history(
        symbol, resolution, days=days, client=client, require_positive=True
    )


def funding_rate_history(
    symbol: str,
    resolution: str = "1h",
    *,
    days: float = 90.0,
    client: DeltaClient | None = None,
) -> pd.DataFrame:
    """Historical funding rate for a perp, one row per settlement period, as an
    OHLCV-shaped frame (use the ``close`` column — the others repeat it).

    Delta's published API docs have no funding-rate-history endpoint, but the
    same ``/v2/history/candles`` route accepts a ``FUNDING:<symbol>`` pseudo-
    symbol and returns real historical values (found by probing the live API,
    2026-09-15 — undocumented, so verify it still works before trusting a
    result built on it). The rate can go negative (crowded shorts paying
    longs), so this skips ``candles()``'s price-positivity filter.
    """
    return _fetch_candle_history(
        f"FUNDING:{symbol}", resolution, days=days, client=client, require_positive=False
    )


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV to a coarser bar (e.g. '15min') on the ``datetime`` column."""
    if df.empty:
        return df
    out = (
        df.set_index("datetime")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
        .reset_index()
    )
    return out


if __name__ == "__main__":  # self-check — no network
    base = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=15, freq="5min", tz="UTC"),
            "open": range(15),
            "high": range(1, 16),
            "low": range(15),
            "close": range(15),
            "volume": [1.0] * 15,
        }
    )
    r = resample(base, "15min")
    assert len(r) == 5 and r.iloc[0]["volume"] == 3.0 and r.iloc[0]["high"] == 3
    assert list(RESOLUTIONS) == list(_CHUNK_DAYS)

    class _FakeClient:
        def get_public(self, path, params=None):
            # one clean row, one negative-close row (a real funding value), one no-timestamp row
            return [
                [1700000000, 0.01, 0.01, 0.01, 0.01, None],
                [1700003600, -0.01, -0.01, -0.01, -0.01, None],
                [None, 1, 1, 1, 1, None],
            ]

    priced = _fetch_candle_history(
        "BTCUSD", "1h", days=1, client=_FakeClient(), require_positive=True
    )
    funding = _fetch_candle_history(
        "FUNDING:BTCUSD", "1h", days=1, client=_FakeClient(), require_positive=False
    )
    assert len(priced) == 1, "positivity filter should drop the negative-close row"
    assert len(funding) == 2 and funding["close"].min() == -0.01, (
        "funding history keeps negative rates"
    )
    print(
        "crypto.delta.market_data self-check ok — resample + resolution tables + funding history filter"
    )
