from __future__ import annotations

from index_ai.instruments import configured_index_keys, instruments


def test_index_universe() -> None:
    assert set(instruments().keys()) == {"NIFTY", "BANKNIFTY", "SENSEX"}
    keys = configured_index_keys()
    assert keys == ("NIFTY", "BANKNIFTY", "SENSEX")
    assert instruments()["SENSEX"].underlying_security_id == 51
