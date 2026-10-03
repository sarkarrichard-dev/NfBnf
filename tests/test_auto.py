from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from index_ai.learning import record_trade_outcome, update_learning
from index_ai.server import app


@pytest.fixture(autouse=True)
def _isolate_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not leave the background scanner running or auto-start on boot."""
    monkeypatch.setenv("AUTO_START_SCANNER", "false")
    from index_ai.scanner import stop_scanner

    asyncio.run(stop_scanner())
    yield
    asyncio.run(stop_scanner())


def test_auto_start_scanner_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTO_START_SCANNER", raising=False)
    from index_ai.scanner import auto_start_scanner_enabled

    assert auto_start_scanner_enabled() is True


def test_bootstrap_scanner_skips_when_dhan_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai.scanner import bootstrap_scanner

    mock_cfg = MagicMock()
    mock_cfg.dhan.ready = False
    monkeypatch.setattr("index_ai.scanner.settings", lambda: mock_cfg)

    result = asyncio.run(bootstrap_scanner(respect_disable_flag=False))
    assert result["started"] is False
    assert result["reason"] == "dhan_not_ready"


def _ready_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = MagicMock()
    cfg.dhan.ready = True
    monkeypatch.setattr("index_ai.scanner.settings", lambda: cfg)


def test_network_down_at_boot_is_not_a_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai import scanner

    _ready_cfg(monkeypatch)
    monkeypatch.setattr(
        "index_ai.dhan_auth.check_dhan_health",
        lambda cfg: {
            "token_ok": False,
            "issues": ["Dhan token check failed: profile: [Errno 11001] getaddrinfo failed"],
        },
    )
    scanner._state.auth_blocked = False
    result = asyncio.run(scanner.bootstrap_scanner(respect_disable_flag=False))
    assert result["reason"] == "network_down" and result["started"] is False
    assert scanner._state.auth_blocked is False  # a dead network must not block the scanner


def test_a_rejected_token_is_still_a_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai import scanner

    _ready_cfg(monkeypatch)
    monkeypatch.setattr(
        "index_ai.dhan_auth.check_dhan_health",
        lambda cfg: {"token_ok": False, "issues": ["Access token rejected by Dhan (401)."]},
    )
    result = asyncio.run(scanner.bootstrap_scanner(respect_disable_flag=False))
    assert result["reason"] == "token_invalid"
    assert scanner._state.auth_blocked is True
    scanner._state.auth_blocked = False


def test_boot_keeps_retrying_until_the_internet_is_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai import scanner

    answers = [
        {"started": False, "reason": "network_down", "message": "getaddrinfo failed"},
        {"started": False, "reason": "network_down", "message": "getaddrinfo failed"},
        {"started": True, "status": {}},
    ]
    calls: list[int] = []

    async def fake_bootstrap(**kw):
        calls.append(1)
        return answers[len(calls) - 1]

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(scanner, "bootstrap_scanner", fake_bootstrap)
    monkeypatch.setattr(scanner, "BOOT_AUTO_START_DELAY_SECONDS", 0)
    monkeypatch.setattr(scanner, "BOOT_NETWORK_RETRY_SECONDS", 0)
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    asyncio.run(scanner.schedule_boot_auto_start())
    assert len(calls) == 3
    assert len(alerts) == 1 and "late" in alerts[0][0]


def test_boot_gives_up_after_the_retry_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai import scanner

    calls: list[int] = []

    async def always_down(**kw):
        calls.append(1)
        return {"started": False, "reason": "network_down", "message": "getaddrinfo failed"}

    monkeypatch.setattr(scanner, "bootstrap_scanner", always_down)
    monkeypatch.setattr(scanner, "BOOT_AUTO_START_DELAY_SECONDS", 0)
    monkeypatch.setattr(scanner, "BOOT_NETWORK_RETRY_SECONDS", 0)
    monkeypatch.setattr(scanner, "BOOT_NETWORK_RETRY_MAX", 3)
    asyncio.run(scanner.schedule_boot_auto_start())
    assert len(calls) == 4  # first try plus three retries, then it stops


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


def test_learning_optimize_endpoint() -> None:
    client = TestClient(app)
    response = client.post("/api/learning/optimize")
    assert response.status_code == 200
    body = response.json()
    assert "oi_insights" in body
    assert "strategy_tuning" in body


def test_learning_endpoint_updates_from_outcome() -> None:
    client = TestClient(app)
    learned = record_trade_outcome("learning-trade-1", -500.0, note="Manual loss")
    assert learned["learning_active"] is True
    assert "effective_min_confidence" in learned

    response = client.get("/api/learning")
    assert response.status_code == 200
    body = response.json()
    assert "learned" in body
    assert body["learned"]["explanation"]

    cleanup = client.post("/api/learning/cleanup")
    assert cleanup.status_code == 200
    assert "feedback_removed" in cleanup.json()["removed"]
    update_learning()
