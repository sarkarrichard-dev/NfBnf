from __future__ import annotations

import pytest

from index_ai.profit_trail import (
    attach_profit_trail_meta,
    evaluate_profit_trail,
    update_profit_trail,
)


@pytest.fixture
def trail_meta(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr("index_ai.trade_lots.get_lots_per_trade", lambda: 1)
    return attach_profit_trail_meta(
        {
            "enable_profit_trail": True,
            "profit_trail_arm_rupees": 500.0,
            "profit_trail_giveback_pct": 0.25,
        }
    )


def test_profit_trail_no_exit_before_arm(trail_meta: dict) -> None:
    meta = update_profit_trail(trail_meta, 400.0)
    assert meta["profit_trail_armed"] is False
    assert meta["profit_trail_hit"] is False


def test_profit_trail_arms_then_exits_on_giveback(trail_meta: dict) -> None:
    meta = update_profit_trail(trail_meta, 800.0)
    assert meta["profit_trail_armed"] is True
    assert meta["peak_mtm_pnl"] == 800.0
    meta = update_profit_trail(meta, 1000.0)
    assert meta["peak_mtm_pnl"] == 1000.0
    assert meta["profit_trail_floor_rupees"] == 750.0
    meta = update_profit_trail(meta, 740.0)
    assert meta["profit_trail_hit"] is True
    _, hit, reason = evaluate_profit_trail(meta, 740.0)
    assert hit is True
    assert reason and "Profit trail" in reason


def test_profit_trail_scales_arm_with_lots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("index_ai.trade_lots.get_lots_per_trade", lambda: 2)
    settings = attach_profit_trail_meta({})
    assert settings["profit_trail_arm_rupees"] == 1000.0
