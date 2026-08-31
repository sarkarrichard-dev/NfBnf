from __future__ import annotations

from index_ai.instruments import (
    configured_index_keys,
    instruments,
    unconfigured_index_keys,
)


def test_index_universe(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_SENSEX", raising=False)  # .env may pause it
    assert set(instruments().keys()) == {"NIFTY", "BANKNIFTY", "SENSEX"}
    keys = configured_index_keys()
    assert keys == ("NIFTY", "BANKNIFTY", "SENSEX")
    assert instruments()["SENSEX"].underlying_security_id == 51


def test_enable_sensex_toggle(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_SENSEX", "false")
    assert configured_index_keys() == ("NIFTY", "BANKNIFTY")
    assert "SENSEX" in unconfigured_index_keys()
    monkeypatch.setenv("ENABLE_SENSEX", "true")
    assert "SENSEX" in configured_index_keys()
