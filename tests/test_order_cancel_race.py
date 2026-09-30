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


# ── Task 2: India entry with a lost reply, settled from Dhan's order book ──


def _fix_base_id(monkeypatch, hexval: str = "aaaaaaaaaa") -> str:
    """Make the uuid4()-derived base_id in dhan_orders deterministic so a
    test can predict the correlation id a placement call will use."""
    import index_ai.dhan_orders as dhan_orders_mod

    class _FixedUUID:
        hex = hexval

    monkeypatch.setattr(dhan_orders_mod.uuid, "uuid4", lambda: _FixedUUID())
    return hexval[:10]


def _bypass_live_order_payload_check(monkeypatch) -> None:
    """Task 2 exercises _place_or_settle / sync, not the quantity/credit gate
    validate_live_order_payload already owns and is already tested elsewhere."""
    import index_ai.execution_safety as safety_mod

    monkeypatch.setattr(
        safety_mod,
        "validate_live_order_payload",
        lambda *a, **k: safety_mod.SafetyCheck(True, "ok", "ok"),
    )


def _buy_call_option(**overrides) -> dict:
    base = dict(
        instrument="NIFTY",
        security_id=54321,
        segment="NSE_FNO",
        transaction_type="BUY",
        option_type="CALL",
        strike=25000,
        quantity=65,
        ltp=100.0,
    )
    base.update(overrides)
    return base


def _bull_put_spread_option(**overrides) -> dict:
    base = dict(
        instrument="NIFTY",
        quantity=65,
        structure="BULL_PUT_SPREAD",
        legs=[
            {
                "security_id": 111,
                "segment": "NSE_FNO",
                "transaction_type": "BUY",
                "option_type": "PUT",
                "strike": 24700,
                "quantity": 65,
                "ltp": 50.0,
            },
            {
                "security_id": 222,
                "segment": "NSE_FNO",
                "transaction_type": "SELL",
                "option_type": "PUT",
                "strike": 24900,
                "quantity": 65,
                "ltp": 90.0,
            },
        ],
    )
    base.update(overrides)
    return base


def _live_settings():
    from index_ai.config import AppSettings, DhanSettings, RiskSettings

    return AppSettings(
        dhan=DhanSettings(
            client_id="1000000001",
            access_token="test-token",
            api_base_url="https://api.dhan.co/v2",
            api_key="",
            api_secret="",
            auth_base_url="https://auth.dhan.co",
            token_expiry="",
        ),
        risk=RiskSettings(
            trading_mode="LIVE",
            allow_live_trading=True,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=1.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )


def test_lost_entry_reply_found_on_book_tracks_normally(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    monkeypatch.setenv("DHAN_ORDER_CONFIRM_SEC", "2")
    base_id = _fix_base_id(monkeypatch)
    correlation_id = f"idxai-{base_id}"[:30]

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.script(
        "GET",
        "/v2/orders",
        [
            {
                "orderId": "555",
                "orderStatus": "TRADED",
                "correlationId": correlation_id,
                "filledQty": 65,
                "quantity": 65,
            }
        ],
    )
    replay.script(
        "GET",
        "/v2/orders/555",
        {"orderId": "555", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )
    replay.script(
        "GET", "/v2/trades", [{"orderId": "555", "tradedQuantity": 65, "tradedPrice": 100.0}]
    )

    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    client = fake_dhan_client(replay, monkeypatch)
    result = place_live_entry_orders(
        client, _buy_call_option(), settings=_live_settings(), signal_action="BUY_CALL"
    )
    assert replay.count("POST", "/v2/orders") == 1
    assert result["order_ids"] == ["555"]
    assert "unconfirmed_correlation_ids" not in result
    assert alerts


def test_lost_entry_reply_not_on_book_propagates_original_error(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    _fix_base_id(monkeypatch)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.script("GET", "/v2/orders", [])  # empty book -- no matching correlationId

    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    client = fake_dhan_client(replay, monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        place_live_entry_orders(
            client, _buy_call_option(), settings=_live_settings(), signal_action="BUY_CALL"
        )
    assert replay.count("POST", "/v2/orders") == 1
    assert not alerts


def test_lost_entry_reply_book_unreadable_returns_pending_unconfirmed(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    base_id = _fix_base_id(monkeypatch)
    correlation_id = f"idxai-{base_id}"[:30]

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )

    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    client = fake_dhan_client(replay, monkeypatch)
    result = place_live_entry_orders(
        client, _buy_call_option(), settings=_live_settings(), signal_action="BUY_CALL"
    )
    assert result["status"] == "LIVE_PENDING"
    assert result["unconfirmed_correlation_ids"] == [correlation_id]
    assert result["order_ids"] == []
    assert replay.count("POST", "/v2/orders") == 1
    assert alerts


def test_spread_hedge_placed_then_short_leg_unconfirmed(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    base_id = _fix_base_id(monkeypatch, "bbbbbbbbbb")
    short_cid = f"idxai-{base_id}-1"[:30]

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "700", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
        (500, {}),
    )
    replay.script(
        "GET",
        "/v2/orders/700",
        {"orderId": "700", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )

    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    client = fake_dhan_client(replay, monkeypatch)
    result = place_live_entry_orders(
        client,
        _bull_put_spread_option(),
        settings=_live_settings(),
        signal_action="SELL_BULL_PUT_SPREAD",
    )
    assert result["status"] == "LIVE_PENDING"
    assert result["unconfirmed_correlation_ids"] == [short_cid]
    assert result["order_ids"] == ["700"]
    assert replay.count("POST", "/v2/orders") == 2
    assert alerts


def test_network_failure_during_confirm_returns_pending_with_order_id(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    _fix_base_id(monkeypatch, "cccccccccc")
    monkeypatch.setenv("DHAN_ORDER_CONFIRM_SEC", "2")

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST", "/v2/orders", {"orderId": "800", "orderStatus": "PENDING", "quantity": 65}
    )
    replay.script("GET", "/v2/trades", [])
    replay.fault(
        "GET",
        "/v2/orders/800",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )

    client = fake_dhan_client(replay, monkeypatch)
    result = place_live_entry_orders(
        client, _buy_call_option(), settings=_live_settings(), signal_action="BUY_CALL"
    )
    assert result["status"] == "LIVE_PENDING"
    assert result["order_ids"] == ["800"]
    assert replay.count("POST", "/v2/orders") == 1


def test_definite_rejection_alerts_and_raises(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_entry_orders

    _bypass_live_order_payload_check(monkeypatch)
    _fix_base_id(monkeypatch, "dddddddddd")

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "900", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
        {"orderStatus": "REJECTED", "omsErrorDescription": "Insufficient margin"},
    )
    replay.script(
        "GET",
        "/v2/orders/900",
        {"orderId": "900", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )

    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    client = fake_dhan_client(replay, monkeypatch)
    with pytest.raises(RuntimeError, match="Dhan rejected"):
        place_live_entry_orders(
            client,
            _bull_put_spread_option(),
            settings=_live_settings(),
            signal_action="SELL_BULL_PUT_SPREAD",
        )
    assert replay.count("POST", "/v2/orders") == 2
    assert alerts


def test_resolver_attaches_order_id_when_now_on_book(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_trade_broker_status

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "GET",
        "/v2/orders",
        [
            {
                "orderId": "555",
                "orderStatus": "TRADED",
                "correlationId": "idxai-resolve1",
                "filledQty": 65,
                "quantity": 65,
            }
        ],
    )
    replay.script(
        "GET", "/v2/trades", [{"orderId": "555", "tradedQuantity": 65, "tradedPrice": 100.0}]
    )

    persisted: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "index_ai.learning.update_trade_status",
        lambda trade_id, status, *, option=None: persisted.append((trade_id, status, option)),
    )

    client = fake_dhan_client(replay, monkeypatch)
    trade = {
        "id": "t-1",
        "status": "LIVE_PENDING",
        "pnl": None,
        "option": {
            "broker_order_ids": [],
            "broker_orders": {"unconfirmed_correlation_ids": ["idxai-resolve1"]},
            "quantity": 65,
            "security_id": 54321,
            "transaction_type": "BUY",
        },
    }
    result = sync_trade_broker_status(trade, client)
    assert persisted, "resolved row must be persisted"
    assert "555" in (result["option"].get("broker_order_ids") or [])
    assert result["option"]["broker_orders"]["unconfirmed_correlation_ids"] == []


def test_resolver_rejects_when_never_reached_dhan(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_trade_broker_status

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script("GET", "/v2/orders", [])  # empty book -- correlation id never appeared

    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade",
        lambda trade_id, reason, *, option=None: rejected.append((trade_id, reason)),
    )

    client = fake_dhan_client(replay, monkeypatch)
    trade = {
        "id": "t-2",
        "status": "LIVE_PENDING",
        "pnl": None,
        "option": {
            "broker_order_ids": [],
            "broker_orders": {"unconfirmed_correlation_ids": ["idxai-resolve2"]},
            "quantity": 65,
            "security_id": 54321,
            "transaction_type": "BUY",
        },
    }
    result = sync_trade_broker_status(trade, client)
    assert result["status"] == "LIVE_REJECTED"
    assert rejected and rejected[0][0] == "t-2"
    assert "never reached Dhan" in rejected[0][1]


def test_resolver_returns_unchanged_when_book_still_unreadable(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_trade_broker_status

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )

    calls: list[str] = []
    monkeypatch.setattr(
        "index_ai.learning.update_trade_status", lambda *a, **k: calls.append("update")
    )
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade", lambda *a, **k: calls.append("reject")
    )

    client = fake_dhan_client(replay, monkeypatch)
    trade = {
        "id": "t-3",
        "status": "LIVE_PENDING",
        "pnl": None,
        "option": {
            "broker_order_ids": [],
            "broker_orders": {"unconfirmed_correlation_ids": ["idxai-resolve3"]},
            "quantity": 65,
            "security_id": 54321,
            "transaction_type": "BUY",
        },
    }
    result = sync_trade_broker_status(trade, client)
    assert result == trade
    assert not calls


def test_book_unreadable_pending_blocks_duplicate_entry(monkeypatch) -> None:
    """execute_plan records the LIVE_PENDING row; validate_open_position then
    blocks a second entry for the same instrument/lane (D-07's "hold the slot",
    enforced by the existing one-position-per-lane gate)."""
    import index_ai.executor as executor_mod
    from index_ai.executor import ExecutionPlan, execute_plan
    from index_ai.execution_safety import SafetyCheck, validate_open_position

    _bypass_live_order_payload_check(monkeypatch)
    _fix_base_id(monkeypatch, "eeeeeeeeee")
    monkeypatch.setenv("DHAN_ORDER_CONFIRM_SEC", "2")

    monkeypatch.setattr(
        executor_mod, "validate_execution_plan", lambda **k: SafetyCheck(True, "ok", "ok")
    )
    monkeypatch.setattr(executor_mod, "score_trade_setup", lambda *a, **k: {})
    monkeypatch.setattr(executor_mod, "score_setup_hf", lambda *a, **k: {})
    monkeypatch.setattr(executor_mod, "extract_features", lambda *a, **k: {})
    monkeypatch.setattr(executor_mod, "build_setup_narrative", lambda *a, **k: "")

    settings = _live_settings()
    monkeypatch.setattr("index_ai.config.settings", lambda: settings)
    monkeypatch.setattr(
        "index_ai.risk.kill_switch_state", lambda r: {"active": False, "reasons": []}
    )
    monkeypatch.setattr("index_ai.market_clock.is_entry_session_timestamp", lambda when=None: True)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    client = fake_dhan_client(replay, monkeypatch)

    plan = ExecutionPlan(
        allowed=True,
        mode="LIVE",
        reason="ok",
        option=_buy_call_option(),
        signal={"action": "BUY_CALL", "confidence": 0.7, "price": 24000},
    )

    result = execute_plan(plan, settings, client)
    assert result["status"] == "LIVE_PENDING"

    check = validate_open_position("NIFTY", "LIVE", action="BUY_CALL")
    assert check.ok is False
    assert check.code == "duplicate_open"


# ── Task 3: retried India exits never re-send a leg already on Dhan's book ──


def _record_live_trade(
    option: dict, *, instrument: str = "NIFTY", action: str = "SELL_BULL_PUT_SPREAD"
) -> dict:
    from index_ai.learning import record_trade

    trade_id = record_trade(
        mode="LIVE",
        instrument=instrument,
        action=action,
        confidence=0.7,
        option=option,
        signal={"action": action, "confidence": 0.7, "price": 24000},
        status="LIVE_TRADED",
    )
    return {
        "id": trade_id,
        "mode": "LIVE",
        "status": "LIVE_TRADED",
        "pnl": None,
        "instrument": instrument,
        "option": option,
    }


def test_spread_exit_retry_skips_confirmed_cover_resends_only_hedge(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    option = {
        "instrument": "NIFTY",
        "quantity": 65,
        "structure": "BULL_PUT_SPREAD",
        "legs": [
            {
                "security_id": 222,
                "segment": "NSE_FNO",
                "transaction_type": "SELL",  # short leg -- exit covers with BUY
                "option_type": "PUT",
                "strike": 24900,
                "quantity": 65,
                "entry_ltp": 90.0,
            },
            {
                "security_id": 111,
                "segment": "NSE_FNO",
                "transaction_type": "BUY",  # hedge leg -- exit sells with SELL
                "option_type": "PUT",
                "strike": 24700,
                "quantity": 65,
                "entry_ltp": 50.0,
            },
        ],
    }
    trade = _record_live_trade(option)
    base_id = str(trade["id"]).replace("-", "")[:12]
    cover_cid = f"idxai-x-{base_id}-0"[:30]
    hedge_cid = f"idxai-x-{base_id}-1"[:30]

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        {
            "orderId": "C1",
            "orderStatus": "TRADED",
            "filledQty": 65,
            "quantity": 65,
            "correlationId": cover_cid,
        },
        {
            "orderStatus": "REJECTED",
            "omsErrorDescription": "Insufficient margin",
            "correlationId": hedge_cid,
        },
    )
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    client = fake_dhan_client(replay, monkeypatch)
    settings = _live_settings()

    first = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert first["status"] == "LIVE_EXIT_FAILED"
    assert replay.count("POST", "/v2/orders") == 2

    # Retry: the book now shows the cover TRADED and the hedge REJECTED.
    replay.script(
        "GET",
        "/v2/orders",
        [
            {
                "orderId": "C1",
                "orderStatus": "TRADED",
                "correlationId": cover_cid,
                "filledQty": 65,
                "quantity": 65,
            },
            {"orderId": "H0", "orderStatus": "REJECTED", "correlationId": hedge_cid},
        ],
    )
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "H1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )

    second = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert second["status"] == "CLOSED"

    cover_posts = [
        c
        for c in replay.calls
        if c[0] == "POST"
        and c[1] == "/v2/orders"
        and (c[2] or {}).get("correlationId") == cover_cid
    ]
    assert len(cover_posts) == 1, "the cover leg must be POSTed at most once overall"
    assert replay.count("POST", "/v2/orders") == 3  # cover + hedge(rejected) + hedge(retry)


def test_single_leg_exit_lost_reply_book_unreadable_then_retries(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    option = {
        "instrument": "NIFTY",
        "security_id": 54321,
        "segment": "NSE_FNO",
        "transaction_type": "BUY",
        "option_type": "CALL",
        "strike": 25000,
        "quantity": 65,
        "entry_ltp": 100.0,
    }
    trade = _record_live_trade(option, action="BUY_CALL")
    base_id = str(trade["id"]).replace("-", "")[:12]
    correlation_id = f"idxai-x-{base_id}"[:30]

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )
    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))
    client = fake_dhan_client(replay, monkeypatch)
    settings = _live_settings()

    first = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert first["status"] == "LIVE_EXIT_FAILED"
    assert replay.count("POST", "/v2/orders") == 1
    assert (
        not alerts
    )  # _place_or_settle's own not-on-book path never fired; book was simply unreadable

    # Retry 1: book still unreadable -> no POST, one alert, still fails.
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )
    second = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert second["status"] == "LIVE_EXIT_FAILED"
    assert replay.count("POST", "/v2/orders") == 1
    assert alerts

    # Retry 2: book now readable and shows the SELL traded -> no POST, closes.
    replay.script(
        "GET",
        "/v2/orders",
        [
            {
                "orderId": "S1",
                "orderStatus": "TRADED",
                "correlationId": correlation_id,
                "filledQty": 65,
                "quantity": 65,
            }
        ],
    )
    third = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert third["status"] == "CLOSED"
    assert replay.count("POST", "/v2/orders") == 1


def test_first_exit_attempt_has_no_extra_order_book_read(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    option = {
        "instrument": "NIFTY",
        "security_id": 54321,
        "segment": "NSE_FNO",
        "transaction_type": "BUY",
        "option_type": "CALL",
        "strike": 25000,
        "quantity": 65,
        "entry_ltp": 100.0,
    }
    trade = _record_live_trade(option, action="BUY_CALL")

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "S1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )
    client = fake_dhan_client(replay, monkeypatch)
    settings = _live_settings()

    result = close_open_trade(
        trade, client=client, app_settings=settings, reason="stop hit", exit_ltp=10.0
    )
    assert result["status"] == "CLOSED"
    assert replay.count("GET", "/v2/orders") == 0


# ── Plan 02-04 Task 1: cancel-while-pending settles from Dhan's book ──


def _record_pending_trade(
    option: dict,
    *,
    instrument: str = "NIFTY",
    action: str = "BUY_CALL",
    status: str = "LIVE_PENDING",
) -> dict:
    from index_ai.learning import record_trade

    trade_id = record_trade(
        mode="LIVE",
        instrument=instrument,
        action=action,
        confidence=0.7,
        option=option,
        signal={"action": action, "confidence": 0.7, "price": 24000},
        status=status,
    )
    return {
        "id": trade_id,
        "mode": "LIVE",
        "status": status,
        "pnl": None,
        "instrument": instrument,
        "option": option,
    }


def _single_leg_pending_option(**overrides) -> dict:
    base = dict(
        instrument="NIFTY",
        security_id=54321,
        segment="NSE_FNO",
        transaction_type="BUY",
        option_type="CALL",
        strike=25000,
        quantity=65,
        entry_ltp=100.0,
        broker_order_ids=["321"],
        broker_orders={"order_ids": ["321"], "unconfirmed_correlation_ids": []},
    )
    base.update(overrides)
    return base


def test_settle_cancel_wins_row_rejected_with_zero_pnl(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    option = _single_leg_pending_option(
        broker_order_ids=["123"], broker_orders={"order_ids": ["123"]}
    )
    trade = _record_pending_trade(option)
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "GET", "/v2/orders", [{"orderId": "123", "orderStatus": "TRANSIT", "quantity": 65}]
    )
    replay.script("GET", "/v2/trades", [])
    replay.script(
        "GET",
        "/v2/orders/123",
        {"orderId": "123", "orderStatus": "TRANSIT", "quantity": 65},
        {"orderId": "123", "orderStatus": "CANCELLED", "quantity": 65},
    )
    replay.script("DELETE", "/v2/orders/123", {"orderId": "123", "orderStatus": "CANCELLED"})

    client = fake_dhan_client(replay, monkeypatch)
    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "CANCELLED"
    assert "cancelled before it filled" in (result.get("reason") or "")
    assert replay.count("DELETE", "/v2/orders/123") == 1
    assert replay.count("POST", "/v2/orders") == 0

    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade["id"],)).fetchone()
    fresh = _row_to_trade(row)
    assert fresh["status"] == "LIVE_REJECTED"
    assert fresh["pnl"] == 0
    assert "cancelled before it filled" in (fresh["option"].get("broker_rejection_reason") or "")


def test_settle_fill_wins_out_of_order_closes_with_one_exit(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    option = _single_leg_pending_option()
    trade = _record_pending_trade(option)
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "GET", "/v2/orders", [{"orderId": "321", "orderStatus": "PENDING", "quantity": 65}]
    )
    replay.script("GET", "/v2/trades", [])
    replay.script(
        "GET", "/v2/orders/321", {"orderId": "321", "orderStatus": "PENDING", "quantity": 65}
    )
    replay.script(
        "DELETE",
        "/v2/orders/321",
        (400, {"errorCode": "DH-000", "errorMessage": "Order already traded"}),
    )

    client = fake_dhan_client(replay, monkeypatch)
    # First read: PENDING; cancel loses the race (400 "already traded"); the
    # re-read below must show TRADED for the fill-wins branch to fire.
    # Swap the GET /v2/orders / GET /v2/trades scripts to their post-cancel
    # values once the first pair has been consumed (BrokerReplay keeps
    # returning the single remaining scripted response forever, so we script
    # a second value up front for each — first consumed by the initial read,
    # second (sticky) consumed by the re-read after the lost cancel race).
    replay.script(
        "GET",
        "/v2/orders",
        [{"orderId": "321", "orderStatus": "PENDING", "quantity": 65}],
        [{"orderId": "321", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65}],
    )
    replay.script(
        "GET",
        "/v2/trades",
        [],
        [{"orderId": "321", "tradedQuantity": 65, "tradedPrice": 100.0}],
    )
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "E1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )

    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "CLOSED"
    assert replay.count("DELETE", "/v2/orders/321") == 1
    assert replay.count("POST", "/v2/orders") == 1


def test_settle_dhan_unreachable_blocks_nothing_sent(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    option = _single_leg_pending_option()
    trade = _record_pending_trade(option)
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
        httpx.ReadTimeout,
    )
    client = fake_dhan_client(replay, monkeypatch)

    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "BLOCKED"
    assert replay.count("DELETE", "/v2/orders/321") == 0
    assert replay.count("POST", "/v2/orders") == 0
    assert alerts

    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade["id"],)).fetchone()
    fresh = _row_to_trade(row)
    assert fresh["status"] == "LIVE_PENDING"
    assert fresh["pnl"] is None


def test_settle_partial_spread_cancels_short_alerts_open_hedge(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))

    option = {
        "instrument": "NIFTY",
        "quantity": 65,
        "structure": "BULL_PUT_SPREAD",
        "broker_order_ids": ["H1", "S1"],
        "broker_orders": {"order_ids": ["H1", "S1"]},
        "legs": [
            {
                "security_id": 111,
                "segment": "NSE_FNO",
                "transaction_type": "BUY",
                "option_type": "PUT",
                "strike": 24700,
                "quantity": 65,
                "entry_ltp": 50.0,
                "broker_order_id": "H1",
            },
            {
                "security_id": 222,
                "segment": "NSE_FNO",
                "transaction_type": "SELL",
                "option_type": "PUT",
                "strike": 24900,
                "quantity": 65,
                "entry_ltp": 90.0,
                "broker_order_id": "S1",
            },
        ],
    }
    trade = _record_pending_trade(option, action="SELL_BULL_PUT_SPREAD")
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "GET",
        "/v2/orders",
        [
            {"orderId": "H1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
            {"orderId": "S1", "orderStatus": "PENDING", "quantity": 65},
        ],
        [
            {"orderId": "H1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
            {"orderId": "S1", "orderStatus": "CANCELLED", "quantity": 65},
        ],
    )
    replay.script(
        "GET",
        "/v2/trades",
        [{"orderId": "H1", "tradedQuantity": 65, "tradedPrice": 50.0}],
    )
    replay.script(
        "GET",
        "/v2/orders/S1",
        {"orderId": "S1", "orderStatus": "PENDING", "quantity": 65},
        {"orderId": "S1", "orderStatus": "CANCELLED", "quantity": 65},
    )
    replay.script("DELETE", "/v2/orders/S1", {"orderId": "S1", "orderStatus": "CANCELLED"})

    client = fake_dhan_client(replay, monkeypatch)
    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "BLOCKED"
    assert replay.count("DELETE", "/v2/orders/S1") == 1
    assert replay.count("POST", "/v2/orders") == 0
    assert alerts
    assert any("H1" in a for a in alerts)

    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade["id"],)).fetchone()
    fresh = _row_to_trade(row)
    assert fresh["status"] == "LIVE_PENDING"
    assert fresh["pnl"] is None


def test_settle_unconfirmed_not_on_book_cancels_with_zero_broker_calls(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    option = _single_leg_pending_option(
        broker_order_ids=[],
        broker_orders={"order_ids": [], "unconfirmed_correlation_ids": ["idxai-abc"]},
    )
    trade = _record_pending_trade(option)
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script("GET", "/v2/orders", [])  # empty book -- correlation id never appeared
    replay.script("GET", "/v2/trades", [])

    client = fake_dhan_client(replay, monkeypatch)
    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "CANCELLED"
    assert replay.count("DELETE", "/v2/orders/321") == 0
    assert replay.count("POST", "/v2/orders") == 0


def test_settle_already_live_traded_runs_normal_close(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    option = _single_leg_pending_option()
    # Journal already shows LIVE_TRADED, but the caller's in-hand `trade` dict
    # is stale (still LIVE_PENDING) -- e.g. fetched just before a concurrent
    # sync updated it. settle must re-read the DB and find LIVE_TRADED.
    trade = _record_pending_trade(option, status="LIVE_TRADED")
    trade["status"] = "LIVE_PENDING"
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "POST",
        "/v2/orders",
        {"orderId": "E1", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65},
    )
    client = fake_dhan_client(replay, monkeypatch)

    result = close_open_trade(trade, client=client, app_settings=settings, reason="manual close")

    assert result["status"] == "CLOSED"
    assert replay.count("GET", "/v2/orders") == 0
    assert replay.count("POST", "/v2/orders") == 1


def test_skip_broker_exit_never_calls_settle_pending_entry(monkeypatch) -> None:
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    calls: list[int] = []

    def _raise_if_called(*a, **k):
        calls.append(1)
        raise AssertionError("settle_pending_entry must not be called when skip_broker_exit=True")

    import index_ai.dhan_orders as dhan_orders_mod

    monkeypatch.setattr(dhan_orders_mod, "settle_pending_entry", _raise_if_called)

    option = _single_leg_pending_option()
    trade = _record_pending_trade(option)
    settings = _live_settings()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    client = fake_dhan_client(replay, monkeypatch)

    result = close_open_trade(
        trade, client=client, app_settings=settings, reason="synced", skip_broker_exit=True
    )

    assert not calls
    assert result["status"] in {"CLOSED", "ALREADY_CLOSED"}
    assert replay.count("GET", "/v2/orders") == 0
    assert replay.count("POST", "/v2/orders") == 0
    assert replay.count("DELETE", "/v2/orders/321") == 0


def test_live_orders_not_enabled_never_calls_settle_pending_entry(monkeypatch) -> None:
    """When live orders are not enabled (disarmed / not in Live mode), the new
    route is a no-op -- this plan never touches that pre-existing path. Current
    (pre- and post-this-plan, unchanged) behaviour for a disarmed pending row is
    to close the journal row using an estimated price without ever contacting
    Dhan -- verified empirically against the real close_open_trade/
    _close_open_trade_locked code, not assumed from the plan's own prose."""
    from index_ai.exit import close_open_trade

    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)

    calls: list[int] = []

    def _raise_if_called(*a, **k):
        calls.append(1)
        raise AssertionError("settle_pending_entry must not be called when live orders disabled")

    import index_ai.dhan_orders as dhan_orders_mod

    monkeypatch.setattr(dhan_orders_mod, "settle_pending_entry", _raise_if_called)

    option = _single_leg_pending_option()
    trade = _record_pending_trade(option)

    from index_ai.config import AppSettings, DhanSettings, RiskSettings

    disarmed_settings = AppSettings(
        dhan=DhanSettings(
            client_id="1000000001",
            access_token="test-token",
            api_base_url="https://api.dhan.co/v2",
            api_key="",
            api_secret="",
            auth_base_url="https://auth.dhan.co",
            token_expiry="",
        ),
        risk=RiskSettings(
            trading_mode="LIVE",
            allow_live_trading=False,  # disarmed -- live_orders_enabled() is False
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=1.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )

    replay = BrokerReplay(load_traffic("dhan_rest"))
    client = fake_dhan_client(replay, monkeypatch)

    result = close_open_trade(
        trade, client=client, app_settings=disarmed_settings, reason="manual close"
    )

    assert not calls
    assert replay.count("GET", "/v2/orders") == 0
    assert replay.count("DELETE", "/v2/orders/321") == 0
    assert replay.count("POST", "/v2/orders") == 0
    # The journal row still gets closed (unchanged pre-existing behaviour for a
    # disarmed row) -- no broker order was ever sent for it.
    assert result["status"] == "CLOSED"


def test_exit_with_no_trade_keeps_random_correlation_ids(monkeypatch) -> None:
    from index_ai.dhan_orders import place_live_exit_orders

    option = {
        "instrument": "NIFTY",
        "security_id": 54321,
        "segment": "NSE_FNO",
        "transaction_type": "BUY",
        "option_type": "CALL",
        "strike": 25000,
    }
    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script("POST", "/v2/orders", {"orderId": "A1", "orderStatus": "TRADED", "quantity": 65})
    client = fake_dhan_client(replay, monkeypatch)
    settings = _live_settings()

    place_live_exit_orders(client, option, settings=settings, quantity=65, trade=None)

    replay.script("POST", "/v2/orders", {"orderId": "A2", "orderStatus": "TRADED", "quantity": 65})
    place_live_exit_orders(client, option, settings=settings, quantity=65, trade=None)

    cids = [
        c[2].get("correlationId") for c in replay.calls if c[0] == "POST" and c[1] == "/v2/orders"
    ]
    assert len(cids) == 2
    assert cids[0] != cids[1]
    assert replay.count("GET", "/v2/orders") == 0
