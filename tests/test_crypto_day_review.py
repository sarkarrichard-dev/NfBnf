"""crypto/day_review.py — summary buckets, local fallback, signature cache."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from crypto import day_review as dr


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "CACHE_PATH", tmp_path / "review.json")
    monkeypatch.setattr("index_ai.llm.enabled", lambda: False)  # force the local path


def _row(**kw):
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "strategy": "ny_n_break",
        "asset": "BTCUSD",
        "side": "short",
        "entry_price": 79000.0,
        "exit_price": 78950.0,
        "entry_time": now,
        "closed_at": now,
        "pnl_usd": 0.4,
        "pnl_inr": 35.0,
        "gross_usd": 0.5,
        "exit_reason": "trailing profit +30% P&L",
        "entry_reason": "inverted-N under 79100",
        "exit_id": "x1",
    }
    base.update(kw)
    return base


def test_bucket_exit_maps_the_real_reason_strings():
    assert dr._bucket_exit("trailing profit +76% P&L (peak +78%)") == "trailing profit"
    assert dr._bucket_exit("trailing stop -5% P&L") == "trailing stop"
    assert dr._bucket_exit("15m inverted-N") == "structural signal"
    assert dr._bucket_exit("cloud re-entry") == "cloud re-entry"
    assert dr._bucket_exit("reached POC") == "reached POC"
    assert dr._bucket_exit(None) == "other"


def test_summary_and_local_review(monkeypatch):
    rows = [
        _row(pnl_usd=0.9, gross_usd=1.0, exit_reason="trailing profit +40% P&L", exit_id="a"),
        _row(
            pnl_usd=-0.3,
            gross_usd=0.2,
            exit_reason="trailing stop -8% P&L",
            strategy="ichimoku",
            exit_id="b",
        ),  # gross-positive, net-negative → fee bleed
        _row(pnl_usd=-0.5, gross_usd=-0.5, exit_reason="15m N", exit_id="c"),
    ]
    monkeypatch.setattr(dr, "_today_rows", lambda: rows)
    s = dr._summary(rows)
    assert s["closed"] == 3 and s["wins"] == 1 and s["losses"] == 2
    assert s["how_trades_ended"]["trailing profit"] == 1
    assert s["how_trades_ended"]["trailing stop"] == 1
    assert s["fee_bled_trades"] == 1
    assert set(s["by_strategy"]) == {"ny_n_break", "ichimoku"}
    # grouped by asset — the default _row() asset is BTCUSD → the "BTC" group
    assert set(s["by_asset"]) == {"BTC"}
    assert s["by_asset"]["BTC"]["trades"] == 3 and s["by_asset"]["BTC"]["wins"] == 1
    assert s["by_asset"]["BTC"]["strategies"] == {"ny_n_break": 2, "ichimoku": 1}

    r = dr._local_review(rows, s)
    assert "trailing profit" in r["narrative"]
    assert any("fee drag" in x for x in r["watch"])
    assert r["by_group"] and r["by_group"][0]["group"] == "BTC"
    assert "improve" in r["by_group"][0]


def test_signature_cache_and_bust(monkeypatch):
    rows = [_row(exit_id="one")]
    monkeypatch.setattr(dr, "_today_rows", lambda: rows)
    a = dr.build_crypto_review(refresh=True)
    b = dr.build_crypto_review()  # same rows → cache hit, same object
    assert a["signature"] == b["signature"]

    rows[0]["pnl_usd"] = 5.0  # a P&L correction must bust the cache
    c = dr.build_crypto_review()
    assert c["signature"] != a["signature"]


def test_empty_day(monkeypatch):
    monkeypatch.setattr(dr, "_today_rows", lambda: [])
    out = dr.build_crypto_review(refresh=True)
    assert out["summary"]["closed"] == 0
    assert out["review"]["narrative"].startswith("No crypto trades")


def test_endpoint_shape():
    from fastapi.testclient import TestClient

    from index_ai.server import app

    body = TestClient(app).get("/api/crypto/day-review").json()
    assert {"summary", "trades", "review", "advisory_only"} <= set(body)
    _ = json.dumps(body)  # serialisable
