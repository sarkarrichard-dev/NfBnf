from index_ai import ticker


class _Cfg:
    dhan = type("D", (), {"ready": False})()


def test_index_quotes_empty_when_dhan_not_ready():
    assert ticker._index_quotes(_Cfg()) == []


def test_crypto_quotes_reads_mark_price_and_change(monkeypatch):
    monkeypatch.setattr(
        "crypto.config.crypto_settings",
        lambda: type("S", (), {"symbols": ("BTCUSD",)})(),
    )
    monkeypatch.setattr(
        "crypto.delta.market_data.ticker",
        lambda sym: {"mark_price": "81234.5", "mark_change_24h": "0.32"},
    )
    rows = ticker._crypto_quotes()
    assert rows == [{"symbol": "BTCUSD", "price": 81234.5, "change_pct": 0.32}]


def test_live_ticker_combines_both(monkeypatch):
    monkeypatch.setattr(
        ticker,
        "_index_quotes",
        lambda cfg: [{"symbol": "NIFTY", "price": 24000.0, "change_pct": None}],
    )
    monkeypatch.setattr(
        ticker,
        "_crypto_quotes",
        lambda: [{"symbol": "BTCUSD", "price": 81000.0, "change_pct": 0.3}],
    )
    out = ticker.live_ticker(_Cfg())
    assert out == {
        "rows": [
            {"symbol": "NIFTY", "price": 24000.0, "change_pct": None},
            {"symbol": "BTCUSD", "price": 81000.0, "change_pct": 0.3},
        ]
    }
