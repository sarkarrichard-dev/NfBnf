"""Isolate strategy env between tests (user .env must not leak into pytest)."""

from __future__ import annotations

import pytest

from index_ai.strategy_params import reload_strategy_params


@pytest.fixture(autouse=True)
def _reset_strategy_params() -> None:
    reload_strategy_params()
    yield
    reload_strategy_params()
