from __future__ import annotations

from index_ai.config import settings
from index_ai.execution_safety import (
    validate_action_matches_option,
    validate_execution_plan,
    validate_live_exit_allowed,
    validate_open_position,
    validate_strategy_coherence,
)
from index_ai.instruments import get_instrument
from index_ai.learning import connect, record_trade


def _bear_call_option() -> dict:
    return {
        "instrument": "NIFTY",
        "structure": "BEAR_CALL_SPREAD",
        "transaction_type": "SELL",
        "option_type": "SPREAD",
        "quantity": 65,
        "security_id": 1,
        "segment": "NSE_FNO",
        "legs": [
            {
                "transaction_type": "SELL",
                "option_type": "CALL",
                "strike": 23500,
                "security_id": 101,
                "segment": "NSE_FNO",
                "quantity": 65,
            },
            {
                "transaction_type": "BUY",
                "option_type": "CALL",
                "strike": 23600,
                "security_id": 102,
                "segment": "NSE_FNO",
                "quantity": 65,
            },
        ],
    }


def test_blocks_conflict_strategy_mode() -> None:
    check = validate_strategy_coherence(
        {"strategy_mode": "conflict", "ema_aligned": "bull"},
        "SELL_BEAR_CALL_SPREAD",
    )
    assert not check.ok
    assert check.code == "strategy_conflict"


def test_blocks_ema_mismatch_on_bear_call() -> None:
    check = validate_strategy_coherence(
        {"strategy_mode": "cpr_trend", "ema_aligned": "bull"},
        "SELL_BEAR_CALL_SPREAD",
    )
    assert not check.ok
    assert check.code == "ema_mismatch"


def test_structure_mismatch_wrong_legs() -> None:
    opt = _bear_call_option()
    opt["structure"] = "BULL_PUT_SPREAD"
    check = validate_action_matches_option("SELL_BEAR_CALL_SPREAD", opt)
    assert not check.ok


def test_live_exit_blocked_without_traded_status() -> None:
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.risk.trading_mode = "LIVE"
    cfg.risk.allow_live_trading = True
    cfg.dhan.ready = True
    trade = {
        "mode": "LIVE",
        "status": "LIVE_SENT",
        "pnl": None,
        "instrument": "NIFTY",
    }
    check = validate_live_exit_allowed(trade, cfg)
    assert not check.ok
    assert check.code == "live_not_filled"


def test_duplicate_open_position_detected() -> None:
    tid = record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.6,
        option={
            "instrument": "NIFTY",
            "security_id": 999,
            "segment": "NSE_FNO",
            "quantity": 65,
            "transaction_type": "BUY",
            "option_type": "CALL",
            "strike": 24000,
        },
        signal={"action": "BUY_CALL", "price": 24000},
        status="PAPER_RECORDED",
    )
    try:
        check = validate_open_position("NIFTY", "PAPER")
        assert not check.ok
        assert check.code == "duplicate_open"
    finally:
        with connect() as db:
            db.execute("DELETE FROM trades WHERE id = ?", (tid,))


def test_full_plan_validation_passes_clean_credit(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(
        "index_ai.market_clock.is_trading_entries_allowed",
        lambda when=None: True,
    )
    cfg = settings()
    inst = get_instrument("NIFTY")
    signal = {
        "action": "SELL_BEAR_CALL_SPREAD",
        "confidence": 0.62,
        "strategy_mode": "ema_cross",
        "ema_aligned": "bear",
        "ema_cross": "DOWN",
        "price": 23400,
    }
    opt = _bear_call_option()
    check = validate_execution_plan(
        app_settings=cfg,
        instrument=inst,
        signal=signal,
        option=opt,
        min_confidence=0.58,
    )
    assert check.ok, check.reason
