"""One feature vector for the crypto lanes.

``entry_snapshot`` is called by ``crypto.lanes`` at entry and stored on the
journal row; ``row_features`` maps a stored row back onto the fixed ``FEATURES``
vector for training and scoring. Keeping both here means capture and training
share one definition.
"""

from __future__ import annotations

from typing import Any

from crypto._util import num as _f

# per-asset one-hot — a fixed set so the vector width is stable; anything else
# lands in ``asset_other``. Extend when a new symbol has enough trades to matter.
_ASSETS: tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "PAXGUSD", "XRPUSD")

FEATURES: tuple[str, ...] = (
    "strategy_ny_n_break",  # 1 = 6 PM lane, 0 = ichimoku
    "side_long",
    "atr_pct",
    "ret_20_pct",
    "entry_hour_utc",
    "dist_ema25_pct",  # 6 PM lane only, else 0
    "dist_vwap_pct",  # 6 PM lane only, else 0
    "dist_cloud_top_pct",  # ichimoku only, else 0
    "dist_kijun_pct",  # ichimoku only, else 0
    *(f"is_{a.lower()}" for a in _ASSETS),
    "asset_other",
)


def entry_snapshot(strat: str, sym: str, frame: Any, side: str) -> dict[str, Any]:
    """Captured at entry, stored on the journal row. Never raises."""
    import pandas as pd

    feats: dict[str, Any] = {
        "venue": "delta",
        "asset": sym,
        "strategy_ny_n_break": 1.0 if strat == "ny_n_break" else 0.0,
        "side_long": 1.0 if side == "long" else 0.0,
    }
    try:
        c = frame["close"].astype(float)
        price = float(c.iloc[-1])
        hi, lo = frame["high"].astype(float), frame["low"].astype(float)
        tr = (hi - lo).rolling(14).mean().iloc[-1]
        feats["atr_pct"] = round(float(tr) / price * 100.0, 4) if price else 0.0
        feats["ret_20_pct"] = (
            round((price / float(c.iloc[-21]) - 1) * 100.0, 4) if len(c) > 21 else 0.0
        )
        feats["entry_hour_utc"] = int(pd.Timestamp(frame["datetime"].iloc[-1]).hour)
        if strat == "ny_n_break":
            from crypto.strategies.indicators import anchored_vwap, ema

            feats["dist_ema25_pct"] = round((price / float(ema(c, 25).iloc[-1]) - 1) * 100.0, 4)
            feats["dist_vwap_pct"] = round(
                (price / float(anchored_vwap(frame).iloc[-1]) - 1) * 100.0, 4
            )
        else:
            from index_ai.strategies.ichimoku import compute_ichimoku

            row = compute_ichimoku(frame).iloc[-1]
            if not pd.isna(row["cloud_top"]):
                feats["dist_cloud_top_pct"] = round(
                    (price / float(row["cloud_top"]) - 1) * 100.0, 4
                )
                feats["dist_kijun_pct"] = round((price / float(row["kijun"]) - 1) * 100.0, 4)
    except Exception:
        pass
    return feats


# Captured on every entry so there is real data to test them on later, but
# deliberately NOT in FEATURES: widening that vector would invalidate the
# saved model, and with ~130 old trades there is nothing yet to say whether
# these help. ``row_features`` ignores them; promote one into FEATURES only
# after a measured out-of-sample test on the forward-collected rows.
MICRO_KEYS: tuple[str, ...] = ("obi_top5", "funding_rate", "open_interest")


def _num_or_none(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN -> None


def microstructure_snapshot(book: Any, tick: Any) -> dict[str, float]:
    """Order-book imbalance, funding rate and open interest from the Delta
    book/ticker the scan already fetched. Pure, never raises; a reading that
    is missing or unparseable is simply left out (never written as 0, which
    would read as "balanced"/"no funding").

    ``obi_top5`` = (bid size - ask size) / (bid size + ask size) over the top
    five levels: +1 all buyers lined up, -1 all sellers. ``funding_rate`` and
    ``open_interest`` are stored exactly as Delta reports them."""
    out: dict[str, float] = {}
    try:
        bids = [_num_or_none(x.get("size")) for x in (book or {}).get("bids", [])[:5]]
        asks = [_num_or_none(x.get("size")) for x in (book or {}).get("asks", [])[:5]]
        bid_sz = sum(v for v in bids if v is not None and v > 0)
        ask_sz = sum(v for v in asks if v is not None and v > 0)
        if bid_sz + ask_sz > 0:
            out["obi_top5"] = round((bid_sz - ask_sz) / (bid_sz + ask_sz), 4)
    except Exception:
        pass
    try:
        t = tick or {}
        fr = _num_or_none(t.get("funding_rate"))
        if fr is not None:
            out["funding_rate"] = fr
        oi = _num_or_none(t.get("oi"))
        if oi is not None and oi >= 0:
            out["open_interest"] = oi
    except Exception:
        pass
    return out


def row_features(row: dict[str, Any]) -> dict[str, float] | None:
    """Map a journal row (or a proposed trade) onto the fixed vector. None if
    the entry snapshot is missing entirely."""
    src = dict(row.get("features") or {})
    if not src and "strategy_ny_n_break" not in row:
        return None
    if not src:
        src = row  # a proposed trade passed flat
    asset = str(src.get("asset") or row.get("asset") or "").upper()
    out = {
        "strategy_ny_n_break": _f(src.get("strategy_ny_n_break")),
        "side_long": _f(src.get("side_long")),
        "atr_pct": _f(src.get("atr_pct")),
        "ret_20_pct": _f(src.get("ret_20_pct")),
        "entry_hour_utc": _f(src.get("entry_hour_utc")),
        "dist_ema25_pct": _f(src.get("dist_ema25_pct")),
        "dist_vwap_pct": _f(src.get("dist_vwap_pct")),
        "dist_cloud_top_pct": _f(src.get("dist_cloud_top_pct")),
        "dist_kijun_pct": _f(src.get("dist_kijun_pct")),
        "asset_other": 1.0 if asset and asset not in _ASSETS else 0.0,
    }
    for a in _ASSETS:
        out[f"is_{a.lower()}"] = 1.0 if asset == a else 0.0
    return {k: _f(out.get(k)) for k in FEATURES}


def label(row: dict[str, Any]) -> int | None:
    """1 = net-profitable close (USD), 0 = not. None if still open / no pnl."""
    v = row.get("pnl_usd")
    if v is None:
        return None
    return 1 if _f(v) > 0 else 0


if __name__ == "__main__":  # self-check — no I/O
    import pandas as pd

    n = 40
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="1h", tz="UTC"),
            "open": range(n),
            "high": [x + 2 for x in range(n)],
            "low": [x - 2 for x in range(n)],
            "close": range(n),
            "volume": [1.0] * n,
        }
    )
    snap = entry_snapshot("ny_n_break", "BTCUSD", frame, "long")
    assert snap["asset"] == "BTCUSD" and snap["strategy_ny_n_break"] == 1.0
    assert "atr_pct" in snap and "dist_ema25_pct" in snap

    row = {"features": snap, "pnl_usd": 12.0}
    v = row_features(row)
    assert v is not None and set(v) == set(FEATURES)
    assert v["is_btcusd"] == 1.0 and v["asset_other"] == 0.0
    assert row_features({"features": {"asset": "DOGEUSD", "side_long": 1.0}})["asset_other"] == 1.0
    assert label(row) == 1 and label({"pnl_usd": -1}) == 0 and label({}) is None
    assert row_features({}) is None

    # microstructure: 50 bid vs 25 ask -> (50-25)/75; strings parse; junk is dropped
    book = {
        "bids": [{"price": 100 - i, "size": 10} for i in range(5)],
        "asks": [{"price": 101 + i, "size": 5} for i in range(5)],
    }
    m = microstructure_snapshot(book, {"funding_rate": "0.0001", "oi": "1234.5"})
    assert m == {"obi_top5": 0.3333, "funding_rate": 0.0001, "open_interest": 1234.5}, m
    assert microstructure_snapshot(None, None) == {}
    assert microstructure_snapshot({"bids": [], "asks": []}, {"oi": "x"}) == {}
    assert set(MICRO_KEYS) == set(m) and not set(MICRO_KEYS) & set(FEATURES)
    # captured-but-untrained: the model's vector must not change
    assert row_features({"features": {**snap, **m}, "pnl_usd": 1.0}) == row_features(row)
    print("crypto.ml.features self-check ok")
