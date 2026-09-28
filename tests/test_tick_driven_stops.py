import asyncio
from types import SimpleNamespace

from index_ai import scanner
from index_ai.config import settings
from index_ai.instruments import get_instrument
from index_ai.trailing import init_trail_meta


def _reset(monkeypatch):
    monkeypatch.setattr(scanner, "_tick_stops", {})
    monkeypatch.setattr(scanner, "_tick_extremes", {})
    monkeypatch.setattr(scanner, "_tick_cross_px", {})
    monkeypatch.setattr(scanner, "_sec_to_key", {13: "NIFTY"})


def test_ticks_move_the_stop_and_a_cross_triggers_an_immediate_check(monkeypatch):
    _reset(monkeypatch)
    fired = []
    monkeypatch.setattr(scanner, "_trigger_tick_check", lambda: fired.append(1))
    scanner._tick_stops["NIFTY"] = [{"dir": 1.0, "dist": 25.0, "best": 23400.0}]
    scanner.on_index_tick({"security_id": 13, "ltp": 23440.0})       # new best -> stop 23415
    assert not fired and scanner._tick_stops["NIFTY"][0]["best"] == 23440.0
    scanner.on_index_tick({"security_id": 13, "ltp": 23416.0})
    assert not fired
    scanner.on_index_tick({"security_id": 13, "ltp": 23414.0})       # through the stop
    assert fired and scanner._tick_cross_px["NIFTY"] == 23414.0


def test_check_closes_at_the_crossing_price_even_after_a_bounce(monkeypatch):
    _reset(monkeypatch)
    meta = init_trail_meta(entry_index_price=23400.0, action="BUY_CALL", transaction_type="BUY",
                           instrument=get_instrument("NIFTY"))
    trade = {"id": "b1", "instrument": "NIFTY", "action": "BUY_CALL", "pnl": None,
             "signal": {"action": "BUY_CALL", "price": 23400.0},
             "option": {"transaction_type": "BUY", "ltp": 150.0, "trail_meta": meta}}
    closed = []

    async def fake_prices(client, open_list=None):
        return {"NIFTY": 23430.0}                                      # bounced back by now

    async def fake_close(t, client, cfg, *, reason, index_price=None):
        closed.append((t["id"], index_price, reason))

    monkeypatch.setattr(scanner, "open_trades_for_mode", lambda mode: [trade])
    monkeypatch.setattr(scanner, "_fetch_index_prices", fake_prices)
    monkeypatch.setattr(scanner, "enrich_open_trade_mtm", lambda t, c: t)
    monkeypatch.setattr(scanner, "update_trade_trail_meta", lambda *a: None)
    monkeypatch.setattr(scanner, "_close_trade", fake_close)
    monkeypatch.setattr(scanner, "TRAIL_INDEX_GAP_SECONDS", 0)
    scanner._tick_extremes["NIFTY"] = [23440.0, 23400.0]               # peak since last check
    scanner._tick_cross_px["NIFTY"] = 23414.0                          # the tick that crossed
    cfg = SimpleNamespace(risk=settings().risk)
    asyncio.run(scanner._check_trails(None, cfg, refresh_supertrend=False))
    assert closed and closed[0][1] == 23414.0
    assert scanner._tick_cross_px == {} and scanner._tick_extremes == {}


def test_open_trade_stops_are_published_for_the_tick_feed(monkeypatch):
    _reset(monkeypatch)
    meta = init_trail_meta(entry_index_price=23400.0, action="BUY_PUT", transaction_type="BUY",
                           instrument=get_instrument("NIFTY"))
    trade = {"id": "p1", "instrument": "NIFTY", "action": "BUY_PUT", "pnl": None,
             "signal": {"action": "BUY_PUT", "price": 23400.0},
             "option": {"transaction_type": "BUY", "ltp": 150.0, "trail_meta": meta}}

    async def fake_prices(client, open_list=None):
        return {"NIFTY": 23400.0}

    monkeypatch.setattr(scanner, "open_trades_for_mode", lambda mode: [trade])
    monkeypatch.setattr(scanner, "_fetch_index_prices", fake_prices)
    monkeypatch.setattr(scanner, "enrich_open_trade_mtm", lambda t, c: t)
    monkeypatch.setattr(scanner, "update_trade_trail_meta", lambda *a: None)
    asyncio.run(scanner._check_trails(None, SimpleNamespace(risk=settings().risk),
                                      refresh_supertrend=False))
    assert scanner._tick_stops["NIFTY"] == [{"dir": -1.0, "dist": 25.0, "best": 23400.0}]
