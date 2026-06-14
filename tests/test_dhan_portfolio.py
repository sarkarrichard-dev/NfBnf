from __future__ import annotations

from index_ai.dhan import unwrap_dhan_record_list
from index_ai.dhan_portfolio import (
    format_trade_book_row,
    normalize_fund_limits,
)


def test_unwrap_dhan_trade_list_nested() -> None:
    raw = {"data": [{"orderId": "1", "orderStatus": "TRADED"}]}
    assert len(unwrap_dhan_record_list(raw)) == 1


def test_normalize_fund_limits_typo_field() -> None:
    funds = normalize_fund_limits(
        {
            "dhanClientId": "1001",
            "availabelBalance": 98440.0,
            "utilizedAmount": 15202.0,
            "withdrawableBalance": 98310.0,
        }
    )
    assert funds["available_balance"] == 98440.0
    assert funds["utilized_amount"] == 15202.0
    assert funds["withdrawable_balance"] == 98310.0


def test_format_trade_book_row_option_leg() -> None:
    row = format_trade_book_row(
        {
            "orderId": "34226060135513",
            "transactionType": "SELL",
            "tradedQuantity": 30,
            "tradedPrice": 1154.5,
            "drvStrikePrice": 54200,
            "drvOptionType": "CALL",
            "exchangeSegment": "NSE_FNO",
            "productType": "MARGIN",
            "exchangeTime": "2026-06-01 10:43:12",
        }
    )
    assert row["order_id"] == "34226060135513"
    assert row["traded_quantity"] == 30
    assert row["traded_price"] == 1154.5
    assert "54200" in row["leg_label"]
    assert "CALL" in row["leg_label"]
