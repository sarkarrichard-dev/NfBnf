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


def test_kill_switch_blocks_after_loss_budget_in_live(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -9500.0)
    monkeypatch.setattr("index_ai.risk.today_live_consecutive_loss_streak", lambda: 0)
    ks = kill_switch_state(_risk(trading_mode="LIVE"))
    assert ks["triggered"] is True
    assert ks["active"] is True


def test_kill_switch_scales_daily_loss_with_lots(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.trade_lots.get_lots_per_trade", lambda: 2)
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -9000.0)
    monkeypatch.setattr("index_ai.risk.today_live_consecutive_loss_streak", lambda: 0)
    ks = kill_switch_state(_risk(trading_mode="LIVE"))
    assert ks["triggered"] is False  # 2 lots × ₹9,000 = ₹18,000 budget
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -18001.0)
    ks2 = kill_switch_state(_risk(trading_mode="LIVE"))
    assert ks2["triggered"] is True


def test_kill_switch_consecutive_losses_not_total(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -500.0)
    monkeypatch.setattr("index_ai.risk.today_live_consecutive_loss_streak", lambda: 3)
    ks = kill_switch_state(_risk(trading_mode="LIVE"))
    assert ks["triggered"] is True
    assert any("consecutive" in r.lower() for r in ks["reasons"])


def test_kill_switch_not_active_in_paper(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -7000.0)
    monkeypatch.setattr("index_ai.risk.today_live_consecutive_loss_streak", lambda: 3)
    ks = kill_switch_state(_risk(trading_mode="PAPER"))
    assert ks["triggered"] is True
    assert ks["active"] is False


def test_execution_gates_ignore_kill_switch_in_paper(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.market_clock.is_trading_entries_allowed", lambda *_a, **_k: True)
    monkeypatch.setattr("index_ai.risk.today_live_realized_pnl", lambda: -7000.0)
    monkeypatch.setattr("index_ai.risk.today_live_consecutive_loss_streak", lambda: 5)
    ok, reason = check_execution_gates(
        risk=_risk(trading_mode="PAPER"),
        signal_action="BUY_CALL",
        transaction_type="BUY",
        confidence=0.8,
        min_confidence=0.5,
    )
    assert ok is True
    assert "kill switch" not in reason.lower()


def test_buy_and_sell_gates(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.market_clock.is_trading_entries_allowed", lambda *_a, **_k: True)
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
