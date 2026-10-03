from __future__ import annotations

import pytest

from index_ai.position_exits import (
    is_intraday_stale_open,
    strategy_exit_reason,
)
from index_ai.strategies.strategy_params import reload_strategy_params


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
    assert is_intraday_stale_open(trade, now=at("15:20")) is False  # square-off window handles it
    assert is_intraday_stale_open(trade, now=at("18:05")) is True  # missed it (2026-09-23)


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
    assert closed == ["PAPER"]  # never tries to order out after hours
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


def test_index_trailed_credit_ignores_signal_flip() -> None:
    # BANKNIFTY credit spread → the 1:1 index trail (credit_spread.SELL_TRAIL_POINTS)
    # owns the exit, not a regime flip
    trade = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "BANKNIFTY", "option": {}}
    assert (
        strategy_exit_reason(trade, "SELL_BULL_PUT_SPREAD", {"day_bias": "TRENDING_BULL"}) is None
    )
    assert strategy_exit_reason(trade, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is None
    # SENSEX has a sell trail too (2026-09-24) — same behaviour
    sensex = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "SENSEX", "option": {}}
    assert strategy_exit_reason(sensex, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is None
    # an index without a sell trail still closes on the flip
    other = {"action": "SELL_BEAR_CALL_SPREAD", "instrument": "FINNIFTY", "option": {}}
    assert strategy_exit_reason(other, "NO_TRADE", {"day_bias": "TRENDING_BULL"}) is not None


# --- Full exit-suppression pin for the sell lane (EXIT-02, 2026-09-02 lesson) ----
# A directional credit spread on an index with a sell trail must ignore the
# signal-flip, CPR-regime and EMA-cross exits; anything else still closes.

_OPPOSED = {
    # position -> (opposing fresh signal, opposing CPR bias, opposing EMA cross)
    "SELL_BEAR_CALL_SPREAD": ("SELL_BULL_PUT_SPREAD", "TRENDING_BULL", "UP"),
    "SELL_BULL_PUT_SPREAD": ("SELL_BEAR_CALL_SPREAD", "TRENDING_BEAR", "DOWN"),
}


@pytest.fixture
def ema_exit_on(monkeypatch):
    """EMA exit and intelligent routing switched on; the cached params are
    rebuilt on the way out so no other test inherits them."""
    with monkeypatch.context() as m:
        m.setenv("EXIT_CREDIT_ON_EMA_CROSS_FLIP", "true")
        m.setenv("REQUIRE_EMA_CROSS_FOR_CREDIT", "true")
        m.setenv("AUTO_INTELLIGENT_ROUTING", "true")
        reload_strategy_params()
        yield
    reload_strategy_params()


def _spread(action: str, inst: str | None, *, nested: bool) -> dict:
    trade: dict = {"action": action, "option": {}}
    if inst is not None:
        if nested:
            trade["option"] = {"instrument": inst}
        else:
            trade["instrument"] = inst
    return trade


def _three_exit_reasons(trade: dict) -> dict[str, str | None]:
    action = trade["action"]
    fresh, bias, cross = _OPPOSED[action]
    return {
        "flip": strategy_exit_reason(trade, fresh, {}),
        "regime": strategy_exit_reason(trade, "NO_TRADE", {"day_bias": bias}),
        "ema": strategy_exit_reason(
            trade, "NO_TRADE", {}, signal={"ema_cross": cross, "ema_aligned": ""}
        ),
    }


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("inst", ["NIFTY", "BANKNIFTY", "SENSEX", " nifty "])
@pytest.mark.parametrize("action", list(_OPPOSED))
def test_index_trailed_credit_suppresses_flip_regime_and_ema_exits(
    ema_exit_on, action, inst, nested
) -> None:
    reasons = _three_exit_reasons(_spread(action, inst, nested=nested))
    assert reasons == {"flip": None, "regime": None, "ema": None}


@pytest.mark.parametrize(
    ("inst", "nested"), [("FINNIFTY", False), ("FINNIFTY", True), (None, False)]
)
@pytest.mark.parametrize("action", list(_OPPOSED))
def test_credit_spread_without_a_sell_trail_still_closes(ema_exit_on, action, inst, nested) -> None:
    reasons = _three_exit_reasons(_spread(action, inst, nested=nested))
    assert all(r for r in reasons.values()), reasons


def test_iron_condor_on_a_trailed_index_is_not_suppressed(ema_exit_on) -> None:
    condor = {"action": "SELL_IRON_CONDOR", "instrument": "NIFTY", "option": {}}
    assert strategy_exit_reason(condor, "NO_TRADE", {}, signal={"ema_cross": "UP"})
    assert strategy_exit_reason(condor, "NO_TRADE", {"day_bias": "TRENDING_BULL"})
