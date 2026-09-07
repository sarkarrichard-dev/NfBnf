"""Phase 1 crypto section — pure logic, no network."""

from __future__ import annotations

import pandas as pd

from crypto import charges
from crypto.config import crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import _query_string, _sign


def test_config_deploy_floor(monkeypatch):
    monkeypatch.setenv("CRYPTO_DEPLOY_USD", "40")
    monkeypatch.setenv("CRYPTO_LEVERAGE", "0.2")
    s = crypto_settings()
    assert s.deploy_usd == 100.0  # hard floor
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
    c = products.get("btcusd")
    assert c and c.usable and c.contract_value == 0.001
    assert products.get("SOLUSD") is None


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
