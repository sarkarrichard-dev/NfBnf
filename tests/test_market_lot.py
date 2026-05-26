from __future__ import annotations

import pytest

from index_ai.instruments import get_instrument, market_lot_size


def test_market_lot_defaults() -> None:
    assert market_lot_size("NIFTY") == 65
    assert market_lot_size("BANKNIFTY") == 30


def test_legacy_env_75_35_maps_to_current_nse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIFTY_LOT_SIZE", "75")
    monkeypatch.setenv("BANKNIFTY_LOT_SIZE", "35")
    assert market_lot_size("NIFTY") == 65
    assert market_lot_size("BANKNIFTY") == 30
    assert get_instrument("NIFTY").lot_size == 65
    assert get_instrument("BANKNIFTY").lot_size == 30
