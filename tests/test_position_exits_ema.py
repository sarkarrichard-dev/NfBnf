from __future__ import annotations

from index_ai.position_exits import strategy_exit_reason
from index_ai.strategies.strategy_params import reload_strategy_params


def test_ema_bull_flip_closes_bear_call(monkeypatch) -> None:
    monkeypatch.setenv("REQUIRE_EMA_CROSS_FOR_CREDIT", "true")
    monkeypatch.setenv("EXIT_CREDIT_ON_EMA_CROSS_FLIP", "true")
    reload_strategy_params()
    trade = {"action": "SELL_BEAR_CALL_SPREAD"}
    reason = strategy_exit_reason(
        trade,
        "NO_TRADE",
        {"day_bias": "TRENDING_BEAR"},
        signal={"ema_cross": "UP", "ema_aligned": "bull"},
    )
    assert reason is not None
    assert "bullish cross" in reason.lower()


def test_cpr_regime_exit_closes_misaligned_credit(monkeypatch) -> None:
    monkeypatch.setenv("REQUIRE_EMA_CROSS_FOR_CREDIT", "true")
    monkeypatch.setenv("AUTO_INTELLIGENT_ROUTING", "false")
    reload_strategy_params()
    trade = {"action": "SELL_BEAR_CALL_SPREAD"}
    reason = strategy_exit_reason(
        trade,
        "NO_TRADE",
        {"day_bias": "TRENDING_BULL"},
        signal={"ema_cross": "", "ema_aligned": "bear"},
    )
    assert reason is not None
    assert "TRENDING_BULL" in reason
