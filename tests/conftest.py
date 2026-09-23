"""Isolate strategy env and DB between tests (user .env must not leak)."""

from __future__ import annotations

import pytest

from index_ai.strategies.strategy_params import reload_strategy_params

# Never load the user's real .env into a test run. _load_env() already skips
# while a test is running, but test modules are imported during collection,
# before that -- and importing index_ai.server loads .env (so the dashboard
# password and other real settings would leak into every test).
import index_ai.config as _config  # noqa: E402

_config.freeze_env()


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EMA_FAST_PERIOD", "8")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    monkeypatch.setenv("BREAKOUT_LOOKBACK", "20")
    monkeypatch.setenv("CPR_NARROW_WIDTH_PCT", "0.35")
    monkeypatch.setenv("CPR_WIDE_WIDTH_PCT", "0.75")
    monkeypatch.setenv("CREDIT_PROFIT_TARGET_PCT", "0.50")
    monkeypatch.setenv("CPR_CREDIT_CONFIDENCE_GATE", "0.45")
    monkeypatch.setenv("ENTRY_CONFIRMATION_BARS", "2")
    monkeypatch.setenv("MAX_CPR_ENTRY_EXTENSION_PCT", "0")
    monkeypatch.setenv("LOSS_GUARD_ENABLED", "true")

    # A handful of tests exercise real production code paths (close_open_trade,
    # crypto.lanes.scan_crypto_paper) that call index_ai.notify internally, and
    # none of them mock notify — only tests/test_notify.py does, deliberately,
    # by re-setting these same two vars itself (monkeypatch composes: a test's
    # own setenv after this fixture's delenv still wins). Without this, any
    # such test sends a REAL Telegram message through the user's real bot
    # whenever the real .env has TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID set — this
    # is the actual explanation for the recurring "BANKNIFTY PE 54600 · +₹142"
    # and "BTCUSD · 6PM · session end" messages traced back to
    # tests/test_exit_credit.py and tests/test_crypto_phase2.py (2026-09-14).
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    # Endpoint tests (e.g. test_admin_auth POSTing /api/trading/mode PAPER and
    # arm-live disarm) reach the real config.update_env_values, which rewrote
    # the user's real .env on every pytest run: TRADING_MODE=PAPER,
    # ALLOW_LIVE_TRADING=false, crypto likewise -- silently disarming live
    # trading -- and failed intermittently on Windows when the running server
    # held .env open. Every write now lands in a throwaway file.
    env_file = tmp_path / ".env"
    env_file.touch()
    monkeypatch.setattr("index_ai.config.ENV_PATH", env_file)
    monkeypatch.setattr("index_ai.strategies.strategy_params.ENV_PATH", env_file)

    db = tmp_path / "trade_memory.sqlite"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    # The scanner and planner write decisions / option-chain snapshots to the
    # market log; keep test runs out of the real memory/market_log.sqlite.
    monkeypatch.setattr("index_ai.market_log.DB_PATH", tmp_path / "market_log.sqlite")
    # the cost model reads measured spreads from memory/ -- tests use the fixed
    # defaults unless they supply their own samples
    monkeypatch.setattr("index_ai.market_context.spread_calib.SAMPLES_PATH",
                        tmp_path / "spread_samples.jsonl")
    import index_ai.charges as _charges

    _charges._measured_cache.clear()

    import index_ai.learning as learning

    learning._schema_initialized = False
    reload_strategy_params()
    yield
    learning._schema_initialized = False
    reload_strategy_params()
