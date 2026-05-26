from __future__ import annotations

from index_ai.config import AppSettings, DhanSettings, RiskSettings
from index_ai.executor import build_execution_plan
from index_ai.instruments import get_instrument
from index_ai.strategy import StrategySignal


def _settings() -> AppSettings:
    return AppSettings(
        dhan=DhanSettings("", "", "", "", "", "", ""),
        risk=RiskSettings(
            trading_mode="PAPER",
            allow_live_trading=False,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=40.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )


def test_credit_plan_uses_credit_confidence_not_learned_buy_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "index_ai.executor.learned_settings",
        lambda: {"effective_min_confidence": 0.65, "min_confidence_adjustment": 0.10},
    )
    monkeypatch.setattr(
        "index_ai.executor.score_trade_setup",
        lambda *a, **k: {"ready": True, "win_probability": 0.1},
    )
    monkeypatch.setattr(
        "index_ai.executor.score_setup_hf",
        lambda *a, **k: {"ready": True, "block_setup": True},
    )

    signal = StrategySignal(
        action="SELL_BULL_PUT_SPREAD",
        reason="test",
        confidence=0.62,
        price=24000.0,
        pivot=23900.0,
        bc=23880.0,
        tc=23920.0,
        ema_fast=24010.0,
        ema_slow=23950.0,
    )
    option = {
        "instrument": "NIFTY",
        "structure": "BULL_PUT_SPREAD",
        "transaction_type": "SELL",
        "quantity": 65,
        "security_id": 1,
        "segment": "NSE_FNO",
        "legs": [
            {"transaction_type": "SELL", "security_id": 1, "segment": "NSE_FNO", "quantity": 65},
            {"transaction_type": "BUY", "security_id": 2, "segment": "NSE_FNO", "quantity": 65},
        ],
    }
    plan = build_execution_plan(
        app_settings=_settings(),
        instrument=get_instrument("NIFTY"),
        signal=signal,
        option=option,
    )
    assert plan.allowed is True
    assert "Credit structure" in plan.reason
