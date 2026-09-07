"""The live-order interlock. These guard real money, so they assert the locks
independently rather than trusting the mode switch alone."""

import pytest

from index_ai.config import ARM_LIVE_PHRASE, _build_risk_settings, feature_flags, set_feature_flag


def _armed(monkeypatch, mode: str, allow: str) -> bool:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", allow)
    return _build_risk_settings().allow_live_trading


def test_live_mode_alone_does_not_arm_orders(monkeypatch):
    # regression: allow_live_trading used to be `mode == "LIVE"`, so the second
    # lock the dashboard advertised did not exist at all
    assert _armed(monkeypatch, "LIVE", "false") is False


def test_allow_flag_alone_does_not_arm_orders(monkeypatch):
    assert _armed(monkeypatch, "PAPER", "true") is False


def test_both_locks_required(monkeypatch):
    assert _armed(monkeypatch, "LIVE", "true") is True


@pytest.mark.parametrize("val", ["1", "yes", "on", "TRUE"])
def test_truthy_spellings_accepted(monkeypatch, val):
    assert _armed(monkeypatch, "LIVE", val) is True


@pytest.mark.parametrize("val", ["", "0", "no", "off", "maybe"])
def test_anything_else_stays_disarmed(monkeypatch, val):
    assert _armed(monkeypatch, "LIVE", val) is False


def test_arming_requires_the_exact_phrase(monkeypatch, tmp_path):
    from index_ai import config

    monkeypatch.setattr(config, "update_env_values", lambda v: None)
    for bad in ("", "yes", "arm", "ARM LIVE ORDER", "ARM  LIVE ORDERS"):
        with pytest.raises(ValueError):
            config.arm_live_trading(bad)
    # case-insensitive but otherwise exact
    assert config.arm_live_trading(ARM_LIVE_PHRASE.lower()) is True


def test_disarming_never_needs_confirmation(monkeypatch):
    from index_ai import config

    monkeypatch.setattr(config, "update_env_values", lambda v: None)
    assert config.disarm_live_trading() is True


def test_switching_to_paper_writes_disarm(monkeypatch):
    from index_ai import config

    written: dict[str, str] = {}
    monkeypatch.setattr(config, "update_env_values", lambda v: written.update(v))
    config.set_trading_mode("PAPER")
    assert written["ALLOW_LIVE_TRADING"] == "false"

    written.clear()
    config.set_trading_mode("LIVE")
    # selecting LIVE must not touch the arming flag in either direction
    assert "ALLOW_LIVE_TRADING" not in written
    assert written["TRADING_MODE"] == "LIVE"


def test_feature_flags_cannot_toggle_anything_financial(monkeypatch):
    from index_ai import config

    monkeypatch.setattr(config, "update_env_values", lambda v: None)
    for forbidden in ("ALLOW_LIVE_TRADING", "TRADING_MODE", "DHAN_ACCESS_TOKEN"):
        with pytest.raises(ValueError):
            set_feature_flag(forbidden, True)
    assert any(f["flag"] == "ENABLE_TICK_FEED" for f in feature_flags())
