from __future__ import annotations

from fastapi.testclient import TestClient

from index_ai.learning import record_trade_outcome, update_learning
from index_ai.server import app


def test_auto_status_and_stop() -> None:
    client = TestClient(app)
    status = client.get("/api/auto/status")
    assert status.status_code == 200
    data = status.json()
    assert data["running"] is False
    assert "NIFTY" in data["indices"]
    assert "BANKNIFTY" in data["indices"]
    stop = client.post("/api/auto/stop")
    assert stop.status_code == 200


def test_heatmap_without_dhan() -> None:
    client = TestClient(app)
    response = client.get("/api/heatmap")
    assert response.status_code == 200
    body = response.json()
    assert "cells" in body


def test_learning_endpoint_updates_from_outcome() -> None:
    client = TestClient(app)
    learned = record_trade_outcome("test-trade-learning", -500.0, note="unit test loss")
    assert learned["learning_active"] is True
    assert "effective_min_confidence" in learned

    response = client.get("/api/learning")
    assert response.status_code == 200
    body = response.json()
    assert "learned" in body
    assert body["learned"]["explanation"]

    cleanup = client.post("/api/learning/cleanup")
    assert cleanup.status_code == 200
    assert cleanup.json()["removed"]["feedback_removed"] >= 1
    update_learning()
