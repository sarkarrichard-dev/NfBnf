from __future__ import annotations

from index_ai.config import RiskSettings
from index_ai.risk import check_execution_gates, kill_switch_state


def _risk(**kwargs: object) -> RiskSettings:
    base = {
        "trading_mode": "PAPER",
        "allow_live_trading": False,
        "allow_option_buying": True,
        "allow_option_selling": True,
        "max_losing_trades_per_day": 3,
        "max_daily_loss_rupees": 6000.0,
        "trailing_stop_index_points": 1.0,
        "min_confidence": 0.55,
        "max_profit_cap_rupees": None,
    }
    base.update(kwargs)
    return RiskSettings(**base)


def test_kill_switch_blocks_after_loss_budget(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.risk.today_realized_pnl", lambda: -7000.0)
    monkeypatch.setattr("index_ai.risk.today_losing_trades_count", lambda: 0)
    ks = kill_switch_state(_risk())
    assert ks["active"] is True


def test_buy_and_sell_gates() -> None:
    ok_buy, _ = check_execution_gates(
        risk=_risk(allow_option_selling=False),
        signal_action="BUY_CALL",
        transaction_type="BUY",
        confidence=0.8,
        min_confidence=0.5,
    )
    assert ok_buy is True
    ok_sell, reason = check_execution_gates(
        risk=_risk(allow_option_selling=False),
        signal_action="BUY_CALL",
        transaction_type="SELL",
        confidence=0.8,
        min_confidence=0.5,
    )
    assert ok_sell is False
    assert "selling" in reason.lower()


