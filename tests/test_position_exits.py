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


def test_today_open_is_stale_only_after_the_close() -> None:
    from datetime import datetime

    trade = {"pnl": None, "created_at": "2026-09-23T11:05:00+05:30"}
    at = lambda hm: datetime.fromisoformat(f"2026-09-23T{hm}:00+05:30")  # noqa: E731
    assert is_intraday_stale_open(trade, now=at("14:00")) is False
    assert is_intraday_stale_open(trade, now=at("15:20")) is False   # square-off window handles it
    assert is_intraday_stale_open(trade, now=at("18:05")) is True    # missed it (2026-09-23)


def test_closed_market_catch_up_flattens_paper_and_alerts_live(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from index_ai import scanner

    stale = [{"pnl": None, "created_at": "2026-09-01T11:00:00+05:30", "instrument": "NIFTY"}]
    monkeypatch.setattr(scanner, "open_trades_for_mode", lambda mode: stale)
    closed, alerts = [], []
    async def fake_close(client, cfg):
        closed.append(cfg.risk.trading_mode)
    monkeypatch.setattr(scanner, "_close_stale_session_positions", fake_close)
    monkeypatch.setattr(scanner, "DhanClient", lambda dhan: None)
    monkeypatch.setattr("index_ai.notify.alert", lambda msg, **k: alerts.append(msg))

    cfg = lambda mode: SimpleNamespace(risk=SimpleNamespace(trading_mode=mode), dhan=None)  # noqa: E731
    asyncio.run(scanner._catch_up_missed_square_off(cfg("PAPER")))
    assert closed == ["PAPER"] and alerts == []
    asyncio.run(scanner._catch_up_missed_square_off(cfg("LIVE")))
    assert closed == ["PAPER"]                        # never tries to order out after hours
    assert alerts and "NIFTY" in alerts[0]


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
    # SENSEX also has premium-trail params now (2026-09-15) — same behaviour
    sensex = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "SENSEX", "option": {}}
    assert strategy_exit_reason(sensex, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is None
    # an index without premium-trail params still closes on the flip
    other = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "FINNIFTY", "option": {}}
    assert strategy_exit_reason(other, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is not None
