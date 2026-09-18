from __future__ import annotations

import pandas as pd

from index_ai.instruments import get_instrument
from index_ai.options_oi import (
    analyze_option_chain,
    apply_oi_to_signal,
    choose_option_from_chain_with_oi,
)
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
        [
            {"close": 22510 + i, "open": 22500 + i, "high": 22520 + i, "low": 22490 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today,
        previous,
        params=StrategyParams(min_directional_ema_spread_pct=0.0),
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    adjusted = apply_oi_to_signal(signal, oi)
    assert adjusted.confidence >= signal.confidence


def test_choose_option_prefers_target_delta_over_raw_liquidity() -> None:
    """2026-09-16: Richard asked to pick the strike by delta/theta, not just
    whichever is busiest. A far-OTM strike can have the most volume and still
    barely react to the underlying — the target-delta band exists exactly to
    avoid that lottery-ticket pick."""
    chain = {
        "data": {
            "oc": {
                # still within the +-2-strike ATM window, but further OTM,
                # low delta — the busiest strike by far
                "22550.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 15,
                        "oi": 500000,
                        "volume": 50000,
                        "greeks": {"delta": 0.12, "theta": -3.0, "gamma": 0.001},
                    }
                },
                # near ATM, delta inside the 0.35-0.55 target band, far less
                # volume than the other strike but still above the min-oi/
                # min-volume floor, so the floor doesn't decide this one
                "22500.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 95,
                        "oi": 5000,
                        "volume": 500,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # the in-band delta strike, not the busiest one
    assert opt["delta"] == 0.45


def test_choose_option_breaks_delta_ties_on_theta_then_liquidity() -> None:
    chain = {
        "data": {
            "oc": {
                "22450.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 100,
                        "oi": 500,
                        "volume": 50,
                        "greeks": {"delta": 0.45, "theta": -12.0, "gamma": 0.003},
                    }
                },
                "22500.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 100,
                        "oi": 100,
                        "volume": 10,
                        "greeks": {"delta": 0.45, "theta": -4.0, "gamma": 0.003},
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    # same delta, security_id 20 bleeds far less per rupee paid despite less OI
    assert opt["security_id"] == 20
    assert opt["theta"] == -4.0


def test_greeks_selection_drops_a_too_thin_strike_even_with_good_delta() -> None:
    """2026-09-16 trading-safety review: ranking by delta first means a
    strike can win purely on delta with almost no real market behind it —
    real numbers from an earlier test (200 OI beating 500,000 OI). A strike
    thinner than buy_greeks_min_oi/min_volume must lose to an in-band strike
    that actually clears the floor, even if its delta is a worse match."""
    chain = {
        "data": {
            "oc": {
                # in-band delta, but far too thin to trust a fill on
                "22500.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 95,
                        "oi": 50,
                        "volume": 5,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                    }
                },
                # delta a bit further from center, but real liquidity behind it
                "22450.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 110,
                        "oi": 20000,
                        "volume": 3000,
                        "greeks": {"delta": 0.58, "theta": -6.0, "gamma": 0.0035},
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # the liquid one, despite the thin strike's better delta


def test_greeks_selection_falls_back_to_liquidity_without_delta_data() -> None:
    """A live-chain gap (Dhan omits greeks) must never stop the buy lane."""
    chain = {
        "data": {
            "oc": {
                "22450.000000": {
                    "ce": {"security_id": 10, "last_price": 120, "oi": 100, "volume": 10}
                },
                "22500.000000": {
                    "ce": {"security_id": 20, "last_price": 95, "oi": 80000, "volume": 5000}
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # liquidity pick, unchanged from before this feature
    assert opt["delta"] is None


def test_greeks_selection_can_be_turned_off(monkeypatch) -> None:
    chain = {
        "data": {
            "oc": {
                "22550.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 15,
                        "oi": 500000,
                        "volume": 50000,
                        "greeks": {"delta": 0.12, "theta": -3.0, "gamma": 0.001},
                    }
                },
                "22500.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 95,
                        "oi": 200,
                        "volume": 20,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    from index_ai.strategies.strategy_params import reload_strategy_params

    monkeypatch.setenv("BUY_USE_GREEKS_STRIKE_SELECTION", "false")
    reload_strategy_params()
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 10  # back to pure liquidity when the feature is off


def test_choose_option_prefers_liquid_strike() -> None:
    chain = {
        "data": {
            "oc": {
                "22450.000000": {
                    "ce": {"security_id": 10, "last_price": 120, "oi": 100, "volume": 10}
                },
                "22500.000000": {
                    "ce": {"security_id": 20, "last_price": 95, "oi": 80000, "volume": 5000}
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
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
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20
    assert opt["oi"] == 80000


def test_greeks_selection_rejects_low_delta_even_with_better_theta() -> None:
    """2026-09-18, Richard: ignore a strike once |delta| < 0.30, on top of
    the existing 0.35-0.55 target band. A candidate below that floor must
    lose even when it would otherwise win the theta/liquidity tiebreak."""
    chain = {
        "data": {
            "oc": {
                # far OTM, delta below the 0.30 floor, but bleeds the least
                # theta per rupee and is the most liquid — would win a pure
                # ranking without the hard cut
                "22550.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 50,
                        "oi": 500000,
                        "volume": 50000,
                        "greeks": {"delta": 0.22, "theta": -1.0, "gamma": 0.002},
                        "implied_volatility": 10.0,
                    }
                },
                "22500.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 95,
                        "oi": 5000,
                        "volume": 500,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                        "implied_volatility": 10.0,
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # the 0.45-delta strike, not the low-delta one
    assert opt["delta"] == 0.45


def test_greeks_selection_rejects_rich_iv_even_with_good_delta() -> None:
    """A strike with a good delta but IV >= buy_greeks_max_iv (15 by
    default) is overpaying for premium — must lose to a strike inside the
    IV cap even if its delta is a slightly worse match."""
    chain = {
        "data": {
            "oc": {
                "22500.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 130,
                        "oi": 20000,
                        "volume": 2000,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                        "implied_volatility": 22.0,  # rich
                    }
                },
                "22450.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 110,
                        "oi": 20000,
                        "volume": 2000,
                        "greeks": {"delta": 0.58, "theta": -6.0, "gamma": 0.0035},
                        "implied_volatility": 12.0,  # under the cap
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # the strike under the IV cap
    assert opt["iv"] == 12.0


def test_greeks_selection_rejects_low_gamma_even_with_good_delta() -> None:
    """A strike with a good delta but gamma <= buy_greeks_min_gamma (0.0012
    by default) won't accelerate even once the trade is working — must lose
    to a strike above the gamma floor even if its delta is a worse match."""
    chain = {
        "data": {
            "oc": {
                "22500.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 95,
                        "oi": 20000,
                        "volume": 2000,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.0008},  # flat
                        "implied_volatility": 10.0,
                    }
                },
                "22450.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 110,
                        "oi": 20000,
                        "volume": 2000,
                        "greeks": {"delta": 0.58, "theta": -6.0, "gamma": 0.0035},
                        "implied_volatility": 10.0,
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # the strike above the gamma floor
    assert opt["gamma"] == 0.0035


def test_greeks_quality_cuts_drop_out_when_iv_data_is_missing() -> None:
    """A live-chain gap (Dhan omits implied_volatility) must never stop the
    buy lane -- the IV cut drops out and the existing delta/theta/liquidity
    ranking still runs, exactly as before this feature."""
    chain = {
        "data": {
            "oc": {
                "22550.000000": {
                    "ce": {
                        "security_id": 10,
                        "last_price": 15,
                        "oi": 500000,
                        "volume": 50000,
                        "greeks": {"delta": 0.12, "theta": -3.0, "gamma": 0.001},
                    }
                },
                "22500.000000": {
                    "ce": {
                        "security_id": 20,
                        "last_price": 95,
                        "oi": 5000,
                        "volume": 500,
                        "greeks": {"delta": 0.45, "theta": -8.0, "gamma": 0.004},
                    }
                },
            }
        }
    }
    inst = get_instrument("NIFTY")
    previous = pd.DataFrame([{"high": 100, "low": 90, "close": 95}])
    today = pd.DataFrame(
        [
            {"close": 22486 + i, "open": 22485 + i, "high": 22490 + i, "low": 22480 + i}
            for i in range(25)
        ]
    )
    signal = cpr_ema_signal(
        today, previous, params=StrategyParams(min_directional_ema_spread_pct=0.0)
    )
    oi = analyze_option_chain(chain, spot=signal.price, instrument=inst)
    opt = choose_option_from_chain_with_oi(chain, signal, inst, oi)
    assert opt["security_id"] == 20  # in-band delta strike, same as before this feature
    assert opt["iv"] is None
