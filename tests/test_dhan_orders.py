from __future__ import annotations

from index_ai.dhan_orders import (
    aggregate_order_status,
    attach_broker_orders,
    build_market_order_payload,
    effective_order_status,
    normalize_order_response,
    order_product_type_for_leg,
    order_response_ok,
    resolve_leg_broker_status,
    sync_trade_broker_status,
    _entry_leg_sequence,
    _exit_leg_sequence,
    _leg_identity,
)


def test_normalize_order_response_top_level() -> None:
    raw = {"orderId": "123", "orderStatus": "PENDING"}
    assert normalize_order_response(raw)["orderId"] == "123"


def test_normalize_order_response_nested() -> None:
    raw = {"data": {"orderId": "456", "orderStatus": "TRADED"}}
    assert normalize_order_response(raw)["orderStatus"] == "TRADED"


def test_order_response_ok_rejects() -> None:
    assert not order_response_ok({"orderStatus": "REJECTED", "orderId": "1"})
    assert order_response_ok({"orderStatus": "PENDING", "orderId": "1"})


def test_market_order_payload_omits_empty_optionals() -> None:
    payload = build_market_order_payload(
        client_id="1000000003",
        security_id=52175,
        exchange_segment="NSE_FNO",
        transaction_type="BUY",
        quantity=65,
        correlation_id="idxai-abc123",
        product_type="INTRADAY",
    )
    assert payload["quantity"] == 65
    assert payload["securityId"] == "52175"
    assert "disclosedQuantity" not in payload
    assert "price" not in payload
    assert "triggerPrice" not in payload
    assert "boProfitValue" not in payload


def test_sell_fno_defaults_to_margin_product() -> None:
    assert order_product_type_for_leg(transaction_type="SELL", exchange_segment="NSE_FNO") == "MARGIN"
    assert order_product_type_for_leg(transaction_type="BUY", exchange_segment="NSE_FNO") in {
        "INTRADAY",
        "MARGIN",
        "CNC",
    }


def test_effective_order_status_filled_qty_is_traded() -> None:
    assert effective_order_status({"orderStatus": "PENDING", "filledQty": 65, "quantity": 65}) == "TRADED"
    assert effective_order_status({"tradedQuantity": 30, "tradedPrice": 44.9}) == "TRADED"


def test_effective_order_status_empty_oms_not_rejected() -> None:
    assert effective_order_status({"orderStatus": "PENDING", "omsErrorDescription": ""}) == "PENDING"


def test_effective_order_status_oms_error_is_rejected() -> None:
    assert effective_order_status({"omsErrorDescription": "Insufficient margin"}) == "REJECTED"


def test_aggregate_order_status_rejected() -> None:
    legs = [
        {"orderStatus": "TRADED", "orderId": "1"},
        {"orderStatus": "REJECTED", "orderId": "2", "omsErrorDescription": "Insufficient margin"},
    ]
    status, reason = aggregate_order_status(legs)
    assert status == "LIVE_REJECTED"
    assert "margin" in (reason or "").lower()


def test_aggregate_order_status_traded() -> None:
    legs = [{"orderStatus": "TRADED"}, {"orderStatus": "PART_TRADED"}]
    status, reason = aggregate_order_status(legs)
    assert status == "LIVE_REJECTED"
    assert reason


def test_normalize_order_response_single_list_row() -> None:
    raw = [{"orderId": 99, "orderStatus": "REJECTED", "omsErrorDescription": "Margin shortfall"}]
    parsed = normalize_order_response(raw)
    assert parsed["orderId"] == 99
    assert effective_order_status(parsed) == "REJECTED"


def test_resolve_rejects_filled_qty_without_trade_book() -> None:
    parsed = {
        "orderId": "34226060135513",
        "orderStatus": "PENDING",
        "filledQty": 65,
        "quantity": 65,
    }
    assert (
        resolve_leg_broker_status(
            "34226060135513",
            parsed,
            trade_fill_index={},
            strict_trade_book=True,
        )
        == "REJECTED"
    )


def test_verify_trade_rejects_missing_broker_position() -> None:
    from index_ai.dhan_orders import verify_trade_against_positions

    trade = {
        "option": {
            "quantity": 30,
            "legs": [
                {"security_id": 1001, "transaction_type": "BUY", "quantity": 30, "strike": 49100, "option_type": "PUT"},
                {"security_id": 1002, "transaction_type": "SELL", "quantity": 30, "strike": 52100, "option_type": "PUT"},
            ],
        }
    }
    ok, reason = verify_trade_against_positions(trade, {1001: 60, 1002: -30})
    assert ok is True

    ok, reason = verify_trade_against_positions(trade, {})
    assert ok is False
    assert reason


def test_sync_rejects_live_traded_without_positions(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade",
        lambda trade_id, reason, *, option=None: rejected.append((trade_id, reason)),
    )
    monkeypatch.setattr(
        "index_ai.learning.live_trades_for_broker_sync",
        lambda: [
            {
                "id": "t-stale",
                "status": "LIVE_TRADED",
                "pnl": None,
                "option": {
                    "broker_order_ids": ["1"],
                    "quantity": 30,
                    "security_id": 999,
                    "transaction_type": "BUY",
                    "legs": [
                        {"security_id": 999, "transaction_type": "BUY", "quantity": 30},
                    ],
                },
            }
        ],
    )
    monkeypatch.setattr(
        "index_ai.dhan_orders.build_order_book_index",
        lambda _c: {"1": {"orderId": "1", "orderStatus": "TRADED", "filledQty": 30, "quantity": 30}},
    )
    monkeypatch.setattr(
        "index_ai.dhan_orders.build_trade_fill_index",
        lambda _c: {"1": {"orderId": "1", "tradedQuantity": 30, "tradedPrice": 100.0}},
    )
    monkeypatch.setattr("index_ai.dhan_orders.build_position_index", lambda _c: {})

    updated = sync_open_live_trades(object())
    assert updated >= 1
    assert rejected and rejected[0][0] == "t-stale"


def test_resolve_rejects_traded_without_trade_book_fill() -> None:
    parsed = {"orderId": "34226060135513", "orderStatus": "TRADED", "filledQty": 0}
    fills: dict = {}
    assert (
        resolve_leg_broker_status(
            "34226060135513",
            parsed,
            trade_fill_index=fills,
            strict_trade_book=True,
        )
        == "REJECTED"
    )


def test_resolve_confirms_fill_in_trade_book() -> None:
    fills = {
        "23226060134413": {"orderId": "23226060134413", "tradedQuantity": 65, "tradedPrice": 44.8},
    }
    parsed = {"orderId": "23226060134413", "orderStatus": "PENDING"}
    assert resolve_leg_broker_status("23226060134413", parsed, trade_fill_index=fills) == "TRADED"


def test_partial_spread_is_rejected() -> None:
    legs = [
        {"orderStatus": "TRADED", "orderId": "1", "filledQty": 65},
        {"orderStatus": "REJECTED", "orderId": "2", "omsErrorDescription": "Margin"},
    ]
    status, reason = aggregate_order_status(legs)
    assert status == "LIVE_REJECTED"


def test_fetch_order_status_uses_trade_fill_index() -> None:
    book = {"242260601288613": {"orderId": "242260601288613", "orderStatus": "PENDING"}}
    fills = {
        "242260601288613": {
            "orderId": "242260601288613",
            "tradedQuantity": 65,
            "tradedPrice": 44.9,
        }
    }

    class NoNetworkClient:
        def get_order(self, order_id: str):
            raise AssertionError("should use trade fill index first")

        def trades_for_order(self, order_id: str):
            raise AssertionError("should use trade fill index first")

    from index_ai.dhan_orders import fetch_order_status

    row = fetch_order_status(
        NoNetworkClient(),
        "242260601288613",
        book_index=book,
        trade_fill_index=fills,
    )
    assert effective_order_status(row) == "TRADED"
    assert float(row.get("tradedPrice") or 0) == 44.9


def test_sync_trade_marks_rejected_from_order_book(monkeypatch) -> None:
    book = {
        "34226060135513": {
            "orderId": "34226060135513",
            "orderStatus": "REJECTED",
            "omsErrorDescription": "Margin",
        },
        "34226060135613": {
            "orderId": "34226060135613",
            "orderStatus": "REJECTED",
            "omsErrorDescription": "Margin",
        },
    }
    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade",
        lambda trade_id, reason, *, option=None: rejected.append((trade_id, reason)),
    )

    trade = {
        "id": "t-sync-1",
        "status": "LIVE_SENT",
        "pnl": None,
        "option": {"broker_order_ids": ["34226060135513", "34226060135613"]},
    }
    out = sync_trade_broker_status(trade, object(), book_index=book, trade_fill_index={})
    assert out["status"] == "LIVE_REJECTED"
    assert out["pnl"] == 0.0
    assert rejected and rejected[0][0] == "t-sync-1"


def test_sync_downgrades_phantom_traded_without_trade_book(monkeypatch) -> None:
    """Order API says TRADED but no fill in GET /trades → reject journal row."""
    book = {
        "34226060135513": {"orderId": "34226060135513", "orderStatus": "TRADED", "filledQty": 0},
        "34226060135613": {"orderId": "34226060135613", "orderStatus": "TRADED", "filledQty": 0},
    }
    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade",
        lambda trade_id, reason, *, option=None: rejected.append((trade_id, reason)),
    )
    monkeypatch.setattr(
        "index_ai.dhan_orders.fetch_order_status",
        lambda client, oid, **kw: book[oid],
    )

    trade = {
        "id": "t-phantom",
        "status": "LIVE_TRADED",
        "pnl": None,
        "option": {
            "broker_order_ids": ["34226060135513", "34226060135613"],
            "legs": [
                {"transaction_type": "SELL", "strike": 54200, "entry_ltp": 1154.5},
                {"transaction_type": "BUY", "strike": 54400, "entry_ltp": 1052.2},
            ],
        },
    }
    out = sync_trade_broker_status(trade, object(), book_index=book, trade_fill_index={})
    assert out["status"] == "LIVE_REJECTED"
    assert out["pnl"] == 0.0
    assert rejected[0][0] == "t-phantom"


def test_sync_partial_spread_rejected(monkeypatch) -> None:
    fills = {
        "23226060134413": {
            "orderId": "23226060134413",
            "tradedQuantity": 65,
            "tradedPrice": 44.8,
        },
    }
    book = {
        "23226060134413": {"orderId": "23226060134413", "orderStatus": "TRADED", "filledQty": 65},
        "23226060134513": {"orderId": "23226060134513", "orderStatus": "TRADED", "filledQty": 0},
    }
    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.learning.reject_live_trade",
        lambda trade_id, reason, *, option=None: rejected.append((trade_id, reason)),
    )
    monkeypatch.setattr(
        "index_ai.dhan_orders.fetch_order_status",
        lambda client, oid, **kw: book[oid],
    )

    trade = {
        "id": "t-partial",
        "status": "LIVE_TRADED",
        "pnl": None,
        "option": {"broker_order_ids": ["23226060134413", "23226060134513"]},
    }
    out = sync_trade_broker_status(trade, object(), book_index=book, trade_fill_index=fills)
    assert out["status"] == "LIVE_REJECTED"
    assert rejected


def test_entry_leg_sequence_buys_first() -> None:
    legs = [
        {"transaction_type": "SELL", "strike": 25000, "option_type": "CALL"},
        {"transaction_type": "BUY", "strike": 25100, "option_type": "CALL"},
    ]
    ordered = _entry_leg_sequence(legs)
    assert ordered[0]["transaction_type"] == "BUY"
    assert ordered[1]["transaction_type"] == "SELL"


def test_entry_leg_sequence_iron_condor_buy_calls_before_sells() -> None:
    legs = [
        {"transaction_type": "SELL", "option_type": "PUT", "strike": 23000},
        {"transaction_type": "SELL", "option_type": "CALL", "strike": 25000},
        {"transaction_type": "BUY", "option_type": "CALL", "strike": 25100},
        {"transaction_type": "BUY", "option_type": "PUT", "strike": 22900},
    ]
    ordered = _entry_leg_sequence(legs)
    txs = [str(l["transaction_type"]) for l in ordered]
    assert txs.index("BUY") < txs.index("SELL")
    assert ordered[0]["option_type"] == "CALL"
    assert ordered[1]["option_type"] == "PUT"
    assert ordered[2]["transaction_type"] == "SELL"
    assert ordered[2]["option_type"] == "CALL"


def test_exit_leg_sequence_covers_shorts_first() -> None:
    legs = [
        {"transaction_type": "BUY", "strike": 25100},
        {"transaction_type": "SELL", "strike": 25000},
    ]
    ordered = _exit_leg_sequence(legs)
    assert ordered[0]["transaction_type"] == "SELL"
    assert ordered[1]["transaction_type"] == "BUY"


def test_attach_broker_orders_matches_by_leg_identity() -> None:
    buy = {"security_id": 1, "transaction_type": "BUY", "option_type": "CALL", "strike": 25100}
    sell = {"security_id": 2, "transaction_type": "SELL", "option_type": "CALL", "strike": 25000}
    option = {"legs": [sell, buy]}
    broker_payload = {
        "order_ids": ["oid-buy", "oid-sell"],
        "order_statuses": ["TRADED", "TRADED"],
        "legs": [
            {"leg": buy, "response": {"orderId": "oid-buy", "tradedPrice": 44.0}},
            {"leg": sell, "response": {"orderId": "oid-sell", "tradedPrice": 80.0}},
        ],
    }
    out = attach_broker_orders(option, broker_payload)
    by_strike = {leg["strike"]: leg for leg in out["legs"]}
    assert by_strike[25100]["broker_order_id"] == "oid-buy"
    assert by_strike[25000]["broker_order_id"] == "oid-sell"
