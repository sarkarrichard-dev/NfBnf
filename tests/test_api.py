from __future__ import annotations

from fastapi.testclient import TestClient

from index_ai.server import app


def test_status_starts_in_safe_paper_mode() -> None:
    client = TestClient(app)

    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.json()
    assert data["trading_mode"] == "PAPER"
    assert data["live_allowed"] is False
    assert data["policy"]["max_losing_trades_per_day"] == 3
    assert data["policy"]["max_profit_cap_rupees"] is None
    assert {item["key"] for item in data["symbols"]} == {"NIFTY", "BANKNIFTY"}
    assert data["timezone"] == "Asia/Kolkata"
    assert "market" in data
    assert data["market"]["timezone"] == "Asia/Kolkata"
