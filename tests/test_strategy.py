from __future__ import annotations

import pandas as pd

from index_ai.instruments import get_instrument
from index_ai.strategy import choose_option_from_chain, cpr_ema_signal
from index_ai.strategy_params import StrategyParams


def test_cpr_ema_buys_call_when_price_is_above_cpr_and_fast_ema() -> None:
    previous = pd.DataFrame(
        [
            {"open": 100, "high": 110, "low": 95, "close": 108},
            {"open": 108, "high": 112, "low": 104, "close": 110},
        ]
    )
    today = pd.DataFrame(
        [{"open": 115 + i, "high": 116 + i, "low": 114 + i, "close": 115 + i} for i in range(25)]
    )

    signal = cpr_ema_signal(
        today,
        previous,
        params=StrategyParams(min_directional_ema_spread_pct=0.0),
    )

    assert signal.action == "BUY_CALL"
    assert signal.confidence >= 0.55


def test_option_chain_uses_exact_dhan_decimal_strike_key() -> None:
    chain = {
        "data": {
            "oc": {
                "22450.000000": {"ce": {"security_id": 111, "last_price": 120}},
                "22500.000000": {"ce": {"security_id": 222, "last_price": 95}},
            }
        }
    }
    instrument = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today,
        previous,
        params=StrategyParams(min_directional_ema_spread_pct=0.0),
    )

    option = choose_option_from_chain(chain, signal, instrument)

    assert option["security_id"] == 222
    assert option["strike"] == 22500
