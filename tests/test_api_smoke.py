from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import trading_ai_engine.server.db as db
    from trading_ai_engine.server import app as app_module

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_memory.sqlite")
    db.init_db()

    with TestClient(app_module.app) as c:
        yield c


def test_health_ok(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    paths = body.get("api_paths") or []
    assert "/api/trading/paper/close" in paths


def test_reject_non_nse_symbol(client: TestClient) -> None:
    r = client.get("/api/research/eval-modes", params={"symbol": "AAPL"})
    assert r.status_code == 400


def test_paper_close_long_pnl(client: TestClient, monkeypatch) -> None:
    import trading_ai_engine.server.db as db
    from trading_ai_engine.trading import paper as paper_mod

    monkeypatch.setattr(paper_mod, "last_daily_close", lambda symbol, lookback_days=15: 110.0)

    oid = db.insert_paper_order(
        {
            "finding_id": None,
            "symbol": "TEST.NS",
            "side": "long",
            "quantity": 2,
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "target": 120.0,
            "notional": 200.0,
            "risk_amount": 10.0,
            "status": "filled_paper",
            "reason": "test",
            "plan": {},
            "brain": {},
            "model_version": "test",
        }
    )
    r = client.post("/api/trading/paper/close", json={"order_id": oid})
    assert r.status_code == 200
    assert r.json().get("status") == "closed_paper"
    assert r.json().get("realized_pnl") == 20.0

    row = db.fetch_paper_order_by_id(oid)
    assert row is not None
    assert row["status"] == "closed_paper"
    assert row["realized_pnl"] == 20.0


def test_paper_close_idempotent_reject(client: TestClient, monkeypatch) -> None:
    import trading_ai_engine.server.db as db
    from trading_ai_engine.trading import paper as paper_mod

    monkeypatch.setattr(paper_mod, "last_daily_close", lambda symbol, lookback_days=15: 100.0)

    oid = db.insert_paper_order(
        {
            "finding_id": None,
            "symbol": "X.NS",
            "side": "long",
            "quantity": 1,
            "entry_price": 50.0,
            "stop_loss": None,
            "target": None,
            "notional": 50.0,
            "risk_amount": 1.0,
            "status": "filled_paper",
            "reason": "t",
            "plan": {},
            "brain": {},
            "model_version": None,
        }
    )
    assert client.post("/api/trading/paper/close", json={"order_id": oid}).status_code == 200
    r2 = client.post("/api/trading/paper/close", json={"order_id": oid})
    assert r2.status_code == 400


def test_paper_close_rejects_non_uuid_order_id(client: TestClient) -> None:
    r = client.post("/api/trading/paper/close", json={"order_id": "not-a-uuid"})
    assert r.status_code == 400
    assert r.json().get("detail") == "invalid_order_id"


def test_paper_history_rejects_invalid_ist_plain_date(client: TestClient) -> None:
    r = client.get("/api/trading/paper/history", params={"date_from": "2025-02-31"})
    assert r.status_code == 400
    assert "invalid date_from" in (r.json().get("detail") or "")
