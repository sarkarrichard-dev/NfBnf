from __future__ import annotations

import pandas as pd

from index_ai.strategies.intelligent_router import choose_auto_engine
from index_ai.strategies.pivot_points import classic_pivot_levels
from index_ai.strategies.strategy_params import reload_strategy_params


def _previous() -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [100, 105], "high": [110, 112], "low": [90, 95], "close": [105, 108]}
    )


def _regime_stub(day_bias: str = "SIDEWAYS"):
    from index_ai.strategies.cpr_regime import CprRegime

    return CprRegime(
        pivot=100.0,
        bc=99.0,
        tc=101.0,
        width=2.0,
        width_pct=0.5,
        width_class="WIDE",
        cpr_type="test",
        virgin_cpr=False,
        price_position="inside",
        day_bias=day_bias,
        note="test",
    )


def test_auto_engine_never_uses_apex(monkeypatch) -> None:
    """Apex removed from AUTO — breakout above R1 still uses EMA/CPR credit only."""
    monkeypatch.setenv("AUTO_INCLUDE_APEX", "true")
    reload_strategy_params()
    previous = _previous()
    _, r1, _, _ = classic_pivot_levels(previous)
    price = r1 + 80
    today = pd.DataFrame(
        {
            "open": [price - i for i in range(30)],
            "high": [price + 3] * 30,
            "low": [price - 4] * 30,
            "close": [price] * 30,
        }
    )
    from index_ai.strategies.strategy import add_indicators
    from index_ai.strategies.ema_cross import analyze_ema_cross

    frame = add_indicators(today, fast=8, slow=20)
    cross = analyze_ema_cross(frame, fast=8, slow=20)
    from index_ai.strategies.strategy_params import get_strategy_params

    choice = choose_auto_engine(
        frame,
        previous,
        _regime_stub("TRENDING_BULL"),
        cross,
        params=get_strategy_params(),
        close=float(price),
    )
    assert choice.engine != "apex"
    assert choice.engine in {"ema_credit", "wait"}


def test_auto_engine_picks_credit_inside_range(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_INCLUDE_APEX", "true")
    reload_strategy_params()
    previous = _previous()
    _, r1, s1, _ = classic_pivot_levels(previous)
    mid = (r1 + s1) / 2
    today = pd.DataFrame(
        {
            "open": [mid] * 25,
            "high": [mid + 1] * 25,
            "low": [mid - 1] * 25,
            "close": [mid] * 25,
        }
    )
    from index_ai.strategies.strategy import add_indicators
    from index_ai.strategies.ema_cross import analyze_ema_cross
    from index_ai.strategies.strategy_params import get_strategy_params

    frame = add_indicators(today, fast=8, slow=20)
    cross = analyze_ema_cross(frame, fast=8, slow=20)
    choice = choose_auto_engine(
        frame,
        previous,
        _regime_stub("SIDEWAYS"),
        cross,
        params=get_strategy_params(),
        close=float(mid),
    )
    assert choice.engine == "ema_credit"
    assert choice.action == "SELL_IRON_CONDOR"
