from __future__ import annotations

from index_ai.backtest_options import estimate_option_pnl_rupees


def test_credit_spread_profitable_when_spot_rises() -> None:
    est = estimate_option_pnl_rupees(
        "SELL_BULL_PUT_SPREAD",
        25000.0,
        25100.0,
        lot_size=65,
        hold_minutes=60.0,
    )
    assert est["proxy_pnl_rupees"] > 0


def test_bearish_credit_loses_when_spot_rises() -> None:
    est = estimate_option_pnl_rupees(
        "SELL_BEAR_CALL_SPREAD",
        25000.0,
        25100.0,
        lot_size=65,
        hold_minutes=60.0,
    )
    assert est["proxy_pnl_rupees"] < 0


def test_option_proxy_deducts_execution_friction() -> None:
    est = estimate_option_pnl_rupees(
        "BUY_CALL",
        25000.0,
        25100.0,
        lot_size=65,
        hold_minutes=30.0,
    )
    assert est["estimated_friction_rupees"] > 0
    assert est["proxy_pnl_rupees"] < est["gross_proxy_pnl_rupees"]
