from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from index_ai.admin_auth import ADMIN_SECRET_HEADER, require_admin_secret
from index_ai.server import app

client = TestClient(app)

_ARMING_ROUTES = [
    ("/api/trading/mode", {"mode": "PAPER"}),
    ("/api/trading/arm-live", {"disarm": True}),
    ("/api/crypto/mode", {"mode": "PAPER"}),
    ("/api/crypto/arm-live", {"disarm": True}),
    # confirm=False everywhere it appears -- these stop at a 400 before ever
    # reaching update_env_values, so the gate is exercised without a real write.
    ("/api/crypto/credentials", {"api_key": "k", "api_secret": "s", "confirm": False}),
]


def test_require_admin_secret_is_a_noop_when_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_API_SECRET", raising=False)
    require_admin_secret(request=None)  # never touches request.headers when unset


@pytest.mark.parametrize("path,body", _ARMING_ROUTES)
def test_arming_routes_work_unmodified_when_secret_unset(monkeypatch, path, body):
    monkeypatch.delenv("ADMIN_API_SECRET", raising=False)
    r = client.post(path, json=body)
    assert r.status_code != 403


@pytest.mark.parametrize("path,body", _ARMING_ROUTES)
def test_arming_routes_reject_missing_or_wrong_secret_once_set(monkeypatch, path, body):
    monkeypatch.setenv("ADMIN_API_SECRET", "s3cr3t")
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, json=body, headers={ADMIN_SECRET_HEADER: "wrong"}).status_code == 403


@pytest.mark.parametrize("path,body", _ARMING_ROUTES)
def test_arming_routes_accept_the_correct_secret(monkeypatch, path, body):
    monkeypatch.setenv("ADMIN_API_SECRET", "s3cr3t")
    r = client.post(path, json=body, headers={ADMIN_SECRET_HEADER: "s3cr3t"})
    assert r.status_code != 403


def test_unrelated_routes_are_never_gated(monkeypatch):
    """The gate is scoped to arming/mode endpoints only -- a read-only route
    must keep working whether or not a secret is configured."""
    monkeypatch.setenv("ADMIN_API_SECRET", "s3cr3t")
    assert client.get("/api/health").status_code == 200
