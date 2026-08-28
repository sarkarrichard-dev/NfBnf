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


def test_long_call_wins_on_a_real_up_move() -> None:
    est = estimate_option_pnl_rupees("BUY_CALL", 25000.0, 25120.0, lot_size=65, hold_minutes=25.0)
    assert est["proxy_pnl_rupees"] > 0


def test_long_call_scratch_trade_is_a_small_loss_not_the_whole_premium() -> None:
    # +5 pts in 15 min: near break-even, must not book the full option premium
    est = estimate_option_pnl_rupees("BUY_CALL", 25000.0, 25005.0, lot_size=65, hold_minutes=15.0)
    assert -900 < est["proxy_pnl_rupees"] < 400


def test_long_put_is_symmetric_to_long_call() -> None:
    call = estimate_option_pnl_rupees("BUY_CALL", 25000.0, 25120.0, lot_size=65, hold_minutes=25.0)
    put = estimate_option_pnl_rupees("BUY_PUT", 25000.0, 24880.0, lot_size=65, hold_minutes=25.0)
    assert abs(call["proxy_pnl_rupees"] - put["proxy_pnl_rupees"]) < 1.0


def test_credit_spread_blowout_approaches_defined_max_loss() -> None:
    est = estimate_option_pnl_rupees(
        "SELL_BULL_PUT_SPREAD", 48000.0, 47200.0, lot_size=30, hold_minutes=180.0
    )
    assert est["proxy_pnl_rupees"] < -5000  # a deep breach must hurt
