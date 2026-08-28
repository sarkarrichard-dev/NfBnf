import pytest

from index_ai.execution_safety import validate_cost_economics
from index_ai.instruments import get_instrument
from index_ai.strategy_params import reload_strategy_params


@pytest.fixture(autouse=True)
def _fresh_params(monkeypatch):
    monkeypatch.setenv("ENFORCE_COST_ECONOMICS", "true")
    monkeypatch.setenv("MIN_EDGE_TO_COST_MULTIPLE", "1.5")
    reload_strategy_params()
    yield
    reload_strategy_params()


BN = lambda: get_instrument("BANKNIFTY")  # noqa: E731


def _condor(credit_points, leg_ltp=100.0):
    return {
        "instrument": "BANKNIFTY",
        "quantity": BN().lot_size,
        "net_credit_points": credit_points,
        "structure": "IRON_CONDOR",
        "legs": [
            {"ltp": leg_ltp, "transaction_type": "SELL"},
            {"ltp": leg_ltp * 0.4, "transaction_type": "BUY"},
            {"ltp": leg_ltp, "transaction_type": "SELL"},
            {"ltp": leg_ltp * 0.4, "transaction_type": "BUY"},
        ],
    }


def test_thin_credit_is_blocked():
    check = validate_cost_economics(_condor(credit_points=3.0), "SELL_IRON_CONDOR", BN())
    assert not check.ok
    assert check.code == "cost_economics"


def test_fat_credit_passes():
    check = validate_cost_economics(_condor(credit_points=80.0), "SELL_IRON_CONDOR", BN())
    assert check.ok


def test_gate_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENFORCE_COST_ECONOMICS", "false")
    reload_strategy_params()
    check = validate_cost_economics(_condor(credit_points=1.0), "SELL_IRON_CONDOR", BN())
    assert check.ok


def test_missing_metrics_abstains():
    opt = {"instrument": "BANKNIFTY", "quantity": BN().lot_size, "structure": "IRON_CONDOR",
           "legs": [{"ltp": 100, "transaction_type": "SELL"}]}
    check = validate_cost_economics(opt, "SELL_IRON_CONDOR", BN())
    assert check.ok  # no net_credit_points -> gate abstains


def test_long_premium_uses_trail_arm_as_edge():
    # tiny option, tiny move-to-arm still clears cost because arm*0.5*qty is large
    opt = {"instrument": "BANKNIFTY", "quantity": BN().lot_size, "ltp": 90.0, "transaction_type": "BUY"}
    check = validate_cost_economics(opt, "BUY_CALL", BN())
    assert check.ok
