from __future__ import annotations

from datetime import timedelta

from index_ai import entry_guard
from index_ai.market_clock import now_ist


def _mock_trades(monkeypatch, trades):
    monkeypatch.setattr(entry_guard, "_todays_trades", lambda _i, _m, _lane: trades)


def test_clear_when_nothing_traded(monkeypatch):
    _mock_trades(monkeypatch, [])
    blocked, why = entry_guard.check(
        "NIFTY", "PAPER", {"width_class": "NORMAL", "price_position": "above_cpr"}
    )
    assert blocked is False and why == ""


def test_daily_cap(monkeypatch):
    now = now_ist()
    _mock_trades(monkeypatch, [{"opened": now, "closed": None}] * 4)
    blocked, why = entry_guard.check("BANKNIFTY", "PAPER", {})
    assert blocked and "daily trade cap for BANKNIFTY sell" in why


def test_reentry_cooldown(monkeypatch):
    now = now_ist()
    _mock_trades(
        monkeypatch,
        [{"opened": now - timedelta(minutes=6), "closed": now - timedelta(minutes=2)}],
    )
    blocked, why = entry_guard.check("NIFTY", "PAPER", {})
    assert blocked and "re-entry cooldown" in why


def test_chop_lockout(monkeypatch):
    now = now_ist()
    closes = [
        {"opened": now - timedelta(minutes=55), "closed": now - timedelta(minutes=50)},
        {"opened": now - timedelta(minutes=40), "closed": now - timedelta(minutes=35)},
        {"opened": now - timedelta(minutes=25), "closed": now - timedelta(minutes=20)},
    ]
    _mock_trades(monkeypatch, closes)
    blocked, why = entry_guard.check("BANKNIFTY", "PAPER", {})
    assert blocked and "chop lockout" in why and "3 round-trips" in why


def test_no_trend_regime(monkeypatch):
    _mock_trades(monkeypatch, [])
    assert entry_guard.check("NIFTY", "PAPER", {"width_class": "WIDE"})[0]
    assert entry_guard.check("NIFTY", "PAPER", {"price_position": "inside_cpr"})[0]


def test_regime_veto_folds_into_check(monkeypatch):
    _mock_trades(monkeypatch, [])
    quiet = {"regime": "QUIET", "allow_buy": False, "allow_sell": False, "reason": "thin"}
    blocked, why = entry_guard.check("NIFTY", "PAPER", {}, lane="sell", regime_read=quiet)
    assert blocked and "QUIET" in why
    # HIGH_VOL stands sell down but not buy
    hv = {"regime": "HIGH_VOL", "allow_buy": True, "allow_sell": False, "reason": "wild"}
    assert entry_guard.regime_blocks_lane(hv, "sell")[0]
    assert not entry_guard.regime_blocks_lane(hv, "buy")[0]
    # RANGE blocks buy only
    rng = {"regime": "RANGE", "allow_buy": False, "allow_sell": True, "reason": "range"}
    assert entry_guard.regime_blocks_lane(rng, "buy")[0]
    assert not entry_guard.regime_blocks_lane(rng, "sell")[0]


def test_regime_gate_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENFORCE_REGIME_GATE", "false")
    quiet = {"regime": "QUIET", "allow_buy": False, "allow_sell": False}
    assert not entry_guard.regime_blocks_lane(quiet, "buy")[0]


def test_daily_cap_env_override_and_clamp(monkeypatch):
    monkeypatch.setenv("DAILY_TRADE_CAP_NIFTY", "2")
    assert entry_guard.daily_cap("NIFTY") == 2
    monkeypatch.setenv("DAILY_TRADE_CAP_NIFTY", "0")
    assert entry_guard.daily_cap("NIFTY") == 1  # clamped — never "block everything"


def test_lane_filter_keeps_sell_only():
    assert entry_guard._lane("SELL_BEAR_CALL_SPREAD") == "sell"
    assert entry_guard._lane("BUY_CALL") == "buy"
