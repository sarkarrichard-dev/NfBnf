import asyncio
from types import SimpleNamespace

from index_ai import scanner


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
    monkeypatch.setattr(scanner, "settings", lambda: SimpleNamespace(dhan=SimpleNamespace(ready=True)))
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
    monkeypatch.setattr(scanner, "settings", lambda: SimpleNamespace(dhan=SimpleNamespace(ready=True)))
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
