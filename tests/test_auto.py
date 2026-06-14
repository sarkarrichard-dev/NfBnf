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
