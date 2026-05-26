from __future__ import annotations

from fastapi.testclient import TestClient

from index_ai.server import app


def test_health_identifies_index_options_ai() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["app"] == "index-options-ai"


def test_oauth_callback_missing_token_redirects_home() -> None:
    client = TestClient(app, follow_redirects=False)
    response = client.get("/dhan/oauth/callback")
    assert response.status_code == 302
    assert "dhan_auth=error" in response.headers["location"]
