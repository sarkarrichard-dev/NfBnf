import asyncio
import time
from types import SimpleNamespace

import pytest

from index_ai import scanner, tick_feed
from index_ai.config import settings
from index_ai.instruments import get_instrument
from index_ai.trailing import init_trail_meta


def test_fast_loop_checks_trails_often_without_the_heavy_refresh(monkeypatch):
    calls = []

    async def fake_check(client, cfg, *, refresh_supertrend=True):
        calls.append(refresh_supertrend)
        if len(calls) >= 3:
            scanner._state.running = False

    monkeypatch.setattr(scanner, "_check_trails", fake_check)
    monkeypatch.setattr(scanner, "TRAIL_FAST_SECONDS", 0)
    monkeypatch.setattr(scanner, "is_market_open", lambda: True)
    monkeypatch.setattr(scanner, "DhanClient", lambda dhan: None)
    monkeypatch.setattr(
        scanner, "settings", lambda: SimpleNamespace(dhan=SimpleNamespace(ready=True))
    )
    monkeypatch.setattr(scanner._state, "running", True)
    monkeypatch.setattr(scanner._state, "auth_blocked", False)
    asyncio.run(scanner._fast_trail_loop())
    assert calls == [False, False, False]


def test_fast_loop_is_idle_when_market_closed(monkeypatch):
    calls = []

    async def fake_check(*a, **k):
        calls.append(1)

    async def stop_after_sleep(_):
        scanner._state.running = False

    monkeypatch.setattr(scanner, "_check_trails", fake_check)
    monkeypatch.setattr(scanner, "is_market_open", lambda: False)
    monkeypatch.setattr(
        scanner, "settings", lambda: SimpleNamespace(dhan=SimpleNamespace(ready=True))
    )
    monkeypatch.setattr(scanner.asyncio, "sleep", stop_after_sleep)
    monkeypatch.setattr(scanner._state, "running", True)
    asyncio.run(scanner._fast_trail_loop())
    assert calls == []


def test_no_open_trades_means_no_dhan_calls(monkeypatch):
    monkeypatch.setattr(scanner, "open_trades_for_mode", lambda mode: [])

    async def must_not(*a, **k):
        raise AssertionError("priced an empty book")

    monkeypatch.setattr(scanner, "_fetch_index_prices", must_not)
    cfg = SimpleNamespace(risk=SimpleNamespace(trading_mode="PAPER"))
    asyncio.run(scanner._check_trails(None, cfg, refresh_supertrend=False))


# --- ORD-04: the 20s price check is the fallback when the tick feed is down ---


def _down_feed_state() -> tick_feed.FeedState:
    """A feed that is 'enabled' but has not ticked in 10 minutes -- status()
    must read this as stalled, same as the dashboard pill will."""
    return tick_feed.FeedState(connected=False, last_tick_at=time.monotonic() - 600)


def _nifty_buy_call_trade() -> dict:
    meta = init_trail_meta(
        entry_index_price=23400.0,
        action="BUY_CALL",
        transaction_type="BUY",
        instrument=get_instrument("NIFTY"),
    )
    return {
        "id": "fallback-1",
        "instrument": "NIFTY",
        "action": "BUY_CALL",
        "pnl": None,
        "signal": {"action": "BUY_CALL", "price": 23400.0},
        "option": {"transaction_type": "BUY", "ltp": 150.0, "trail_meta": meta},
    }


def _wire_check_trails(monkeypatch, trade: dict, fetched_price: float, closed: list):
    async def fake_prices(client, open_list=None):
        return {"NIFTY": fetched_price}

    async def fake_close(t, client, cfg, *, reason, index_price=None):
        closed.append((t["id"], index_price, reason))

    monkeypatch.setattr(scanner, "open_trades_for_mode", lambda mode: [trade])
    monkeypatch.setattr(scanner, "_fetch_index_prices", fake_prices)
    monkeypatch.setattr(scanner, "enrich_open_trade_mtm", lambda t, c: t)
    monkeypatch.setattr(scanner, "update_trade_trail_meta", lambda *a: None)
    monkeypatch.setattr(scanner, "_close_trade", fake_close)
    monkeypatch.setattr(scanner, "TRAIL_INDEX_GAP_SECONDS", 0)


def test_crossed_stop_closes_on_the_price_check_when_the_tick_feed_is_down(monkeypatch):
    monkeypatch.setenv("ENABLE_TICK_FEED", "true")
    monkeypatch.setattr(tick_feed, "_state", _down_feed_state())
    assert tick_feed.status()["stalled"] is True

    # no tick ever crossed the stop -- the tick-driven path is provably out of play
    monkeypatch.setattr(scanner, "_tick_stops", {})
    monkeypatch.setattr(scanner, "_tick_extremes", {})
    monkeypatch.setattr(scanner, "_tick_cross_px", {})

    trade = _nifty_buy_call_trade()
    closed: list = []
    _wire_check_trails(monkeypatch, trade, fetched_price=23370.0, closed=closed)  # stop is 23375

    cfg = SimpleNamespace(risk=settings().risk)
    asyncio.run(scanner._check_trails(None, cfg, refresh_supertrend=False))

    assert closed == [("fallback-1", 23370.0, closed[0][2])] if closed else False
    assert len(closed) == 1


def test_stop_not_crossed_the_fallback_does_not_close_early(monkeypatch):
    monkeypatch.setenv("ENABLE_TICK_FEED", "true")
    monkeypatch.setattr(tick_feed, "_state", _down_feed_state())

    monkeypatch.setattr(scanner, "_tick_stops", {})
    monkeypatch.setattr(scanner, "_tick_extremes", {})
    monkeypatch.setattr(scanner, "_tick_cross_px", {})

    trade = _nifty_buy_call_trade()
    closed: list = []
    _wire_check_trails(monkeypatch, trade, fetched_price=23390.0, closed=closed)  # stop is 23375

    cfg = SimpleNamespace(risk=settings().risk)
    asyncio.run(scanner._check_trails(None, cfg, refresh_supertrend=False))

    assert closed == []


@pytest.mark.parametrize(
    "feed_env, feed_state",
    [
        ("true", tick_feed.FeedState(connected=True, last_tick_at=time.monotonic())),
        ("true", tick_feed.FeedState(connected=False, last_tick_at=time.monotonic() - 600)),
        (None, tick_feed.FeedState()),
    ],
    ids=["tick-feed-live", "tick-feed-stalled", "tick-feed-off"],
)
def test_fast_loop_runs_while_the_tick_feed_is_stalled_or_off(monkeypatch, feed_env, feed_state):
    if feed_env is None:
        monkeypatch.delenv("ENABLE_TICK_FEED", raising=False)
    else:
        monkeypatch.setenv("ENABLE_TICK_FEED", feed_env)
    monkeypatch.setattr(tick_feed, "_state", feed_state)

    calls = []

    async def fake_check(client, cfg, *, refresh_supertrend=True):
        calls.append(refresh_supertrend)
        if len(calls) >= 3:
            scanner._state.running = False

    monkeypatch.setattr(scanner, "_check_trails", fake_check)
    monkeypatch.setattr(scanner, "TRAIL_FAST_SECONDS", 0)
    monkeypatch.setattr(scanner, "is_market_open", lambda: True)
    monkeypatch.setattr(scanner, "DhanClient", lambda dhan: None)
    monkeypatch.setattr(
        scanner, "settings", lambda: SimpleNamespace(dhan=SimpleNamespace(ready=True))
    )
    monkeypatch.setattr(scanner._state, "running", True)
    monkeypatch.setattr(scanner._state, "auth_blocked", False)
    asyncio.run(scanner._fast_trail_loop())
    assert calls == [False, False, False]
