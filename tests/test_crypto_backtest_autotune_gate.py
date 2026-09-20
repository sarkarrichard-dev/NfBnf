from __future__ import annotations

from index_ai.server import crypto_backtest_autotune_enabled


def test_backtest_autotune_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CRYPTO_BACKTEST_AUTOTUNE", raising=False)
    assert crypto_backtest_autotune_enabled() is False


def test_backtest_autotune_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("CRYPTO_BACKTEST_AUTOTUNE", "true")
    assert crypto_backtest_autotune_enabled() is True


def test_backtest_autotune_off_for_falsy_values(monkeypatch):
    monkeypatch.setenv("CRYPTO_BACKTEST_AUTOTUNE", "false")
    assert crypto_backtest_autotune_enabled() is False
