"""Isolate strategy env and DB between tests (user .env must not leak)."""

from __future__ import annotations

import pytest

from index_ai.strategies.strategy_params import reload_strategy_params


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EMA_FAST_PERIOD", "8")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    monkeypatch.setenv("BREAKOUT_LOOKBACK", "20")
    monkeypatch.setenv("CPR_NARROW_WIDTH_PCT", "0.35")
    monkeypatch.setenv("CPR_WIDE_WIDTH_PCT", "0.75")
    monkeypatch.setenv("CREDIT_PROFIT_TARGET_PCT", "0.50")
    monkeypatch.setenv("CPR_CREDIT_MIN_CONFIDENCE", "0.58")
    monkeypatch.setenv("ENTRY_CONFIRMATION_BARS", "2")
    monkeypatch.setenv("MAX_CPR_ENTRY_EXTENSION_PCT", "0")
    monkeypatch.setenv("LOSS_GUARD_ENABLED", "true")

    db = tmp_path / "trade_memory.sqlite"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)

    import index_ai.learning as learning

    learning._schema_initialized = False
    reload_strategy_params()
    yield
    learning._schema_initialized = False
    reload_strategy_params()
