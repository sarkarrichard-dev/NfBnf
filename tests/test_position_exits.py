from __future__ import annotations

from index_ai.position_exits import (
    is_intraday_stale_open,
    strategy_exit_reason,
)


def test_stale_open_from_prior_day() -> None:
    trade = {
        "pnl": None,
        "created_at": "2026-05-30T10:00:00+05:30",
    }
    assert is_intraday_stale_open(trade) is True


def test_today_open_not_stale() -> None:
    from index_ai.market_clock import now_ist_iso

    trade = {"pnl": None, "created_at": now_ist_iso()}
    assert is_intraday_stale_open(trade) is False


def test_regime_exit_bear_position_in_bull_regime(monkeypatch) -> None:
    monkeypatch.setenv("REQUIRE_EMA_CROSS_FOR_CREDIT", "false")
    from index_ai.strategies.strategy_params import reload_strategy_params

    reload_strategy_params()
    trade = {"action": "SELL_BEAR_CALL_SPREAD"}
    reason = strategy_exit_reason(trade, "NO_TRADE", {"day_bias": "TRENDING_BULL"})
    assert reason is not None
    assert "TRENDING_BULL" in reason


def test_signal_flip_exit() -> None:
    trade = {"action": "SELL_BEAR_CALL_SPREAD"}
    reason = strategy_exit_reason(trade, "SELL_BULL_PUT_SPREAD", {"day_bias": "TRENDING_BULL"})
    assert reason is not None


def test_premium_trailed_credit_ignores_signal_flip() -> None:
    # BANKNIFTY credit spread → premium_trail owns the exit, not a regime flip
    trade = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "BANKNIFTY", "option": {}}
    assert (
        strategy_exit_reason(trade, "SELL_BULL_PUT_SPREAD", {"day_bias": "TRENDING_BULL"}) is None
    )
    assert strategy_exit_reason(trade, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is None
    # an index without premium-trail params still closes on the flip
    other = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "SENSEX", "option": {}}
    assert strategy_exit_reason(other, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is not None
