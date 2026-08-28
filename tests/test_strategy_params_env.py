from __future__ import annotations

from index_ai.strategies.strategy_params import get_strategy_params, reload_strategy_params


def test_strategy_params_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CPR_NARROW_WIDTH_PCT", "0.28")
    monkeypatch.setenv("CPR_WIDE_WIDTH_PCT", "0.90")
    monkeypatch.setenv("CREDIT_WING_STRIKES", "3")
    params = reload_strategy_params()
    assert params.cpr_narrow_width_pct == 0.28
    assert params.cpr_wide_width_pct == 0.90
    assert params.credit_wing_strikes == 3
    reload_strategy_params()
