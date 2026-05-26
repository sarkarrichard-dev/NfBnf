from __future__ import annotations

from fastapi.testclient import TestClient

from index_ai.analytics import build_analytics
from index_ai.server import app


def test_analytics_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/analytics")
    assert response.status_code == 200
    data = response.json()
    assert "today" in data
    assert "week" in data
    assert "month" in data
    assert "daily_series" in data
    assert "policy" in data


def test_trading_mode_toggle() -> None:
    client = TestClient(app)
    response = client.post("/api/trading/mode", json={"mode": "PAPER"})
    assert response.status_code == 200
    assert response.json()["trading_mode"] == "PAPER"


def test_build_analytics_shape() -> None:
    data = build_analytics()
    assert "trades" in data
    assert data["overview"]["trades"] >= 0
