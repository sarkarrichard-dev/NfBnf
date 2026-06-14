from __future__ import annotations

from fastapi.testclient import TestClient

from index_ai.server import app


def test_dhan_account_requires_credentials(monkeypatch) -> None:
    from index_ai import server

    class FakeDhan:
        ready = False
        access_token = ""
        app_credentials_ready = False
        can_generate_consent = False

    class FakeCfg:
        dhan = FakeDhan()

    monkeypatch.setattr(server, "settings", lambda: FakeCfg())
    client = TestClient(app)
    response = client.get("/api/dhan/account")
    assert response.status_code == 400
