"""Phase 1 crypto section — pure logic, no network."""

from __future__ import annotations

import pandas as pd

from crypto import charges
from crypto.config import crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import _query_string, _sign


def test_config_sizing_knobs(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARGIN_PER_POSITION_USD", "0")
    monkeypatch.setenv("CRYPTO_DEPLOY_USD", "-5")
    monkeypatch.setenv("CRYPTO_LEVERAGE", "0.2")
    s = crypto_settings()
    assert s.margin_per_position_usd == 1.0  # min 1
    assert s.deploy_usd == 0.0  # cap floored at 0 (0 = no cap)
    assert s.leverage == 1.0  # min
    assert s.base_url.startswith("https://") and not s.base_url.endswith("/")


def test_signature_prehash_order():
    # prehash = METHOD + timestamp + path + query + body
    sig = _sign("sec", "GET" + "1700000000" + "/v2/wallet/balances" + "" + "")
    assert len(sig) == 64
    assert _query_string({"size": 1, "side": "buy", "order_type": "market_order"}) == (
        "?order_type=market_order&side=buy&size=1"
    )


def test_resample_ohlcv():
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=15, freq="5min", tz="UTC"),
            "open": range(15),
            "high": range(1, 16),
            "low": range(15),
            "close": range(15),
            "volume": [2.0] * 15,
        }
    )
    r = market_data.resample(df, "15min")
    assert len(r) == 5
    assert r.iloc[0]["volume"] == 6.0
    assert r.iloc[0]["open"] == 0 and r.iloc[0]["close"] == 2


def test_products_parse_from_blob(monkeypatch):
    fake = {
        "schema": products._SCHEMA,
        "fetched_at": 9e18,  # far future so it never refreshes
        "contracts": {
            "BTCUSD": {
                "symbol": "BTCUSD",
                "product_id": 27,
                "contract_value": 0.001,
                "tick_size": 0.5,
                "min_size": 1,
                "max_leverage": 100,
            }
        },
    }
    monkeypatch.setattr(products, "_mem", fake)
    # the fake cache doesn't cover CRYPTO_SYMBOLS and is tiny, so guard the
    # freshness checks and any live re-pull
    monkeypatch.setattr(products, "_covers_configured", lambda _b: True)
    monkeypatch.setattr(products, "_MIN_LIVE_CONTRACTS", 1)
    monkeypatch.setattr(products, "_load_disk", lambda: None)
    monkeypatch.setattr(products, "_pull", lambda _c: fake)
    c = products.get("btcusd")
    assert c and c.usable and c.contract_value == 0.001
    assert products.get("SOLUSD") is None


def _spec(sym, pid):
    return {
        "symbol": sym,
        "product_id": pid,
        "contract_value": 1.0,
        "tick_size": 0.1,
        "min_size": 1,
        "max_leverage": 100,
        "last_seen": 1e18,
    }


def test_products_merge_keeps_symbols_a_partial_pull_dropped(monkeypatch):
    import time

    monkeypatch.setattr(products, "_covers_configured", lambda _b: True)
    monkeypatch.setattr(products, "_MIN_LIVE_CONTRACTS", 2)
    cached = {
        "schema": products._SCHEMA,
        "fetched_at": time.time() - 60,
        "contracts": {
            "BTCUSD": _spec("BTCUSD", 27),
            "ETHUSD": _spec("ETHUSD", 3136),
            "SOLUSD": _spec("SOLUSD", 14823),
        },
    }
    monkeypatch.setattr(products, "_mem", dict(cached))
    monkeypatch.setattr(products, "_load_disk", lambda: dict(cached))
    monkeypatch.setattr(products, "_save_disk", lambda _b: None)

    # a pull that only returns BTC + ETH must not lose SOL
    monkeypatch.setattr(
        products,
        "_pull",
        lambda _c: {
            "schema": products._SCHEMA,
            "fetched_at": time.time(),
            "contracts": {"BTCUSD": _spec("BTCUSD", 27), "ETHUSD": _spec("ETHUSD", 3136)},
        },
    )
    blob = products._blob(force=True)
    assert set(blob["contracts"]) == {"BTCUSD", "ETHUSD", "SOLUSD"}

    # a pull below the sanity floor is thrown away entirely — the good cache stands
    monkeypatch.setattr(
        products,
        "_pull",
        lambda _c: {
            "schema": products._SCHEMA,
            "fetched_at": time.time(),
            "contracts": {"BTCUSD": _spec("BTCUSD", 27)},
        },
    )
    blob = products._blob(force=True)
    assert set(blob["contracts"]) == {"BTCUSD", "ETHUSD", "SOLUSD"}


def test_charges_fee_and_spread():
    # 0.05% taker * 1.18 GST on $1000 notional, one side
    assert abs(charges.fee_usd(1000) - 0.59) < 1e-6
    # measured half-spread from a book beats the bps fallback
    book = {"bids": [{"price": 100.0}], "asks": [{"price": 101.0}]}
    assert charges.half_spread_usd("BTCUSD", 100, book=book) == 0.5


def test_crypto_routes_mounted():
    from index_ai.server import app

    paths = {r.path for r in app.routes}
    assert "/api/crypto/health" in paths
    assert "/api/crypto/credentials" in paths
    assert "/api/crypto/status" in paths
