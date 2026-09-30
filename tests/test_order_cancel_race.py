"""India duplicate / lost-reply / idempotent-exit fault tests (ORD-01/ORD-02,
plan 02-03 — the Dhan mirror of plan 02-01's Delta lost-entry-settlement
tests). Drives the real DhanClient/dhan_orders/executor/exit code against
replayed real Dhan traffic (tests/_fake_brokers.py, D-03) with injected
transport faults, never a hand-rolled stub of the client itself.
"""

from __future__ import annotations

import httpx
import pytest

from _fake_brokers import BrokerReplay, fake_dhan_client, load_traffic

pytestmark = pytest.mark.filterwarnings("ignore")


def _order_kwargs(**overrides):
    base = dict(
        security_id=12345,
        exchange_segment="NSE_FNO",
        transaction_type="BUY",
        quantity=65,
        correlation_id="idxai-test1",
    )
    base.update(overrides)
    return base


# ── Task 1: HTTP layer — a lost POST reply sends one order, DELETE/cancel ──


def test_lost_post_reply_readtimeout_sends_order_once(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    # Queue four faults (what the old 4x-retry code would have consumed) —
    # only one should actually be used since a mutating call no longer retries.
    replay.fault(
        "POST",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )
    client = fake_dhan_client(replay, monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        client.place_market_order(**_order_kwargs())
    assert replay.count("POST", "/v2/orders") == 1


def test_lost_post_reply_connecterror_sends_order_once(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ConnectError)
    client = fake_dhan_client(replay, monkeypatch)
    with pytest.raises(httpx.ConnectError):
        client.place_market_order(**_order_kwargs(correlation_id="idxai-test2"))
    assert replay.count("POST", "/v2/orders") == 1


def test_get_still_retries_on_transport_error(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("GET", "/v2/orders", httpx.ReadTimeout)
    replay.script("GET", "/v2/orders", {"data": []})
    client = fake_dhan_client(replay, monkeypatch)
    result = client.list_today_orders()
    assert result == []
    assert replay.count("GET", "/v2/orders") == 2


def test_cancel_order_sends_one_delete(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script("DELETE", "/v2/orders/123", {"orderId": "123", "orderStatus": "CANCELLED"})
    client = fake_dhan_client(replay, monkeypatch)
    result = client.cancel_order("123")
    assert replay.count("DELETE", "/v2/orders/123") == 1
    assert result.get("orderId") == "123"


def test_cancel_order_empty_id_raises(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    client = fake_dhan_client(replay, monkeypatch)
    with pytest.raises(ValueError):
        client.cancel_order("")


def test_429_on_post_is_still_retried(monkeypatch) -> None:
    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        (429, {"errorCode": "805", "errorMessage": "rate limited"}),
        {"orderId": "999", "orderStatus": "TRADED"},
    )
    client = fake_dhan_client(replay, monkeypatch)
    result = client.place_market_order(**_order_kwargs(correlation_id="idxai-test3"))
    assert replay.count("POST", "/v2/orders") == 2
    assert result.get("orderId") == "999"


def test_401_on_post_refreshes_token_once(monkeypatch) -> None:
    from index_ai.dhan_errors import DhanAuthError

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script("POST", "/v2/orders", (401, {"errorCode": "DH-901", "errorMessage": "bad token"}))
    client = fake_dhan_client(replay, monkeypatch)

    refresh_calls: list[bool] = []

    def fake_refresh(settings, *, force=False, reason=""):
        refresh_calls.append(force)
        return {}

    import index_ai.dhan_auth as _dhan_auth_mod

    monkeypatch.setattr(_dhan_auth_mod, "auto_refresh_dhan_token", fake_refresh)

    with pytest.raises(DhanAuthError):
        client.place_market_order(**_order_kwargs(correlation_id="idxai-test4"))
    assert True in refresh_calls  # the reactive 401 refresh ran with force=True
    assert replay.count("POST", "/v2/orders") == 1
