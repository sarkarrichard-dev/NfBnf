from __future__ import annotations

import pandas as pd

from index_ai.instruments import get_instrument
from index_ai.options_oi import analyze_option_chain, apply_oi_to_signal, choose_option_from_chain_with_oi
from index_ai.strategies.strategy import cpr_ema_signal
from index_ai.strategies.strategy_params import StrategyParams


def test_oi_boosts_aligned_call_signal() -> None:
    chain = {
        "data": {
            "oc": {
                "22500.000000": {
                    "ce": {"security_id": 1, "last_price": 100, "oi": 50000, "volume": 1000},
                    "pe": {"security_id": 2, "last_price": 80, "oi": 10000, "volume": 500},
                }
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [{"close": 22510 + i, "open": 22500 + i, "high": 22520 + i, "low": 22490 + i} for i in range(25)]
    )
    signal = cpr_ema_signal(
        today,
        previous,
        params=StrategyParams(min_directional_ema_spread_pct=0.0),
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    adjusted = apply_oi_to_signal(signal, oi)
    assert adjusted.confidence >= signal.confidence


def test_choose_option_prefers_liquid_strike() -> None:
    chain = {
        "data": {
            "oc": {
                "22450.000000": {"ce": {"security_id": 10, "last_price": 120, "oi": 100, "volume": 10}},
                "22500.000000": {"ce": {"security_id": 20, "last_price": 95, "oi": 80000, "volume": 5000}},
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [{"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i} for i in range(25)]
    )
    signal = cpr_ema_signal(
        today,
        previous,
        params=StrategyParams(min_directional_ema_spread_pct=0.0),
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20
    assert opt["oi"] == 80000
