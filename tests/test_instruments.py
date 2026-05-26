from __future__ import annotations

from index_ai.instruments import configured_index_keys, instruments


def test_only_nifty_and_banknifty() -> None:
    assert set(instruments().keys()) == {"NIFTY", "BANKNIFTY"}
    keys = configured_index_keys()
    assert "NIFTY" in keys
    assert "BANKNIFTY" in keys
