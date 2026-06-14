from __future__ import annotations

from index_ai.config import candle_interval_minutes


def test_candle_interval_default_one(monkeypatch) -> None:
    monkeypatch.delenv("CANDLE_INTERVAL_MINUTES", raising=False)
    assert candle_interval_minutes() == "1"


def test_candle_interval_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "5")
    assert candle_interval_minutes() == "5"


def test_bars_for_minutes_scales_with_interval(monkeypatch) -> None:
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "1")
    from importlib import reload
    import index_ai.config as cfg

    reload(cfg)
    assert cfg.bars_for_minutes(15) == 15
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "5")
    reload(cfg)
    assert cfg.bars_for_minutes(15) == 3
