"""/api/crypto endpoints — read paths (no network) + config validation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from crypto import journal
from crypto.delta import products
from index_ai.server import app

client = TestClient(app)

_FAKE_LISTED = ["BTCUSD", "ETHUSD", "SOLUSD", "PAXGUSD", "XRPUSD"]


def test_status_ok(monkeypatch):
    monkeypatch.setattr(products, "available_symbols", lambda *a, **k: list(_FAKE_LISTED))
    r = client.get("/api/crypto/status")
    assert r.status_code == 200
    body = r.json()
    assert "BTCUSD" in body["symbols"] and "ETHUSD" in body["symbols"]
    assert body["available_symbols"] == _FAKE_LISTED
    assert "deploy_usd" in body["sizing"]
    assert body["session_ist"]["start"] and body["session_ist"]["end"]


def test_journal_and_day(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "j.jsonl")
    journal.journal(
        {
            "day": "2026-09-07",
            "strategy": "ny_n_break",
            "asset": "BTCUSD",
            "pnl_usd": 5.0,
            "pnl_inr": 440.0,
        }
    )
    j = client.get("/api/crypto/journal?limit=10").json()
    assert len(j["trades"]) == 1 and j["trades"][0]["pnl_usd"] == 5.0

    d = client.get("/api/crypto/day").json()
    assert "ny_n_break" in d and "ichimoku" in d
    assert d["ny_n_break"]["trades"] >= 0


def test_positions_no_creds(monkeypatch):
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_API_SECRET", raising=False)
    monkeypatch.setattr(journal, "load_state", lambda: {"ny_n_break:BTCUSD": {"position": None}})
    r = client.get("/api/crypto/positions").json()
    assert r["paper"] == [] and r["live"] == []
    assert r["open_unrealized_usd"] == 0.0


def test_config_validates_and_clamps(monkeypatch):
    saved = {}
    monkeypatch.setattr("index_ai.config.update_env_values", lambda v: saved.update(v))
    r = client.post(
        "/api/crypto/config",
        json={"deploy_usd": 20, "leverage": 999, "max_concurrent": 50},
    )
    assert r.status_code == 200
    assert saved["CRYPTO_DEPLOY_USD"] == "100.0"  # floored
    assert saved["CRYPTO_LEVERAGE"] == "100.0"  # clamped to product max
    assert saved["CRYPTO_MAX_CONCURRENT"] == "10"

    empty = client.post("/api/crypto/config", json={})
    assert empty.status_code == 400


def test_config_symbols_validated_against_contract_master(monkeypatch):
    saved = {}
    monkeypatch.setattr("index_ai.config.update_env_values", lambda v: saved.update(v))
    monkeypatch.setattr(products, "available_symbols", lambda *a, **k: list(_FAKE_LISTED))

    # unknown symbol dropped, order + dedup preserved
    r = client.post(
        "/api/crypto/config",
        json={"symbols": ["solusd", "BTCUSD", "SOLUSD", "DOGEUSD"]},
    )
    assert r.status_code == 200
    assert saved["CRYPTO_SYMBOLS"] == "SOLUSD,BTCUSD"

    # nothing Delta lists → 400
    bad = client.post("/api/crypto/config", json={"symbols": ["DOGEUSD"]})
    assert bad.status_code == 400


def test_credentials_requires_confirm():
    r = client.post(
        "/api/crypto/credentials", json={"api_key": "k", "api_secret": "s", "confirm": False}
    )
    assert r.status_code == 400
