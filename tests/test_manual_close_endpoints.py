"""Dashboard "Close" / "Close all" buttons for the India index-options lane —
the endpoints reuse close_open_trade (already covered by test_exit_credit.py
for the concurrency/live-order path); this covers the endpoint wiring itself:
missing trade, already-closed trade, and close-all touching every open row."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from index_ai.learning import record_trade
from index_ai.server import app


@pytest.fixture(autouse=True)
def _no_real_dhan_client(monkeypatch):
    # The real .env has real Dhan credentials, so cfg.dhan.ready is True even
    # in tests — without this, close_open_trade's own internal LTP lookup
    # (option_ltp_with_retry, with real retries/backoff) fires real, slow
    # network calls against a fake test security_id. These trades are all
    # PAPER, so close_open_trade never needs a live client to close them
    # safely — with client=None it falls straight to its own PnL estimate.
    monkeypatch.setattr("index_ai.server.DhanClient", lambda *a, **k: None)


def _open_trade(**overrides) -> str:
    option = {
        "transaction_type": "BUY",
        "option_type": "CALL",
        "strike": 24000,
        "ltp": 100.0,
        "security_id": 1,
        "segment": "NSE_FNO",
        "quantity": 75,
    }
    kwargs = dict(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.7,
        option=option,
        signal={"price": 24000.0},
        status="PAPER_RECORDED",
    )
    kwargs.update(overrides)
    return record_trade(**kwargs)


def test_close_missing_trade_id_is_a_422() -> None:
    client = TestClient(app)
    resp = client.post("/api/trades/positions/close", json={})
    assert resp.status_code == 422


def test_close_unknown_trade_id_is_a_404() -> None:
    client = TestClient(app)
    resp = client.post("/api/trades/positions/close", json={"trade_id": "not-a-real-id"})
    assert resp.status_code == 404


def test_close_an_open_trade_marks_it_closed() -> None:
    trade_id = _open_trade()
    client = TestClient(app)
    resp = client.post("/api/trades/positions/close", json={"trade_id": trade_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"


def test_closing_an_already_closed_trade_is_a_clean_no_op() -> None:
    trade_id = _open_trade()
    client = TestClient(app)
    first = client.post("/api/trades/positions/close", json={"trade_id": trade_id})
    assert first.json()["status"] == "CLOSED"

    second = client.post("/api/trades/positions/close", json={"trade_id": trade_id})
    assert second.status_code == 200
    assert second.json()["status"] == "ALREADY_CLOSED"


def test_close_all_closes_every_open_trade() -> None:
    a = _open_trade()
    b = _open_trade(instrument="BANKNIFTY")
    client = TestClient(app)
    resp = client.post("/api/trades/positions/close-all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempted"] == 2
    assert body["results"][a]["status"] == "CLOSED"
    assert body["results"][b]["status"] == "CLOSED"
