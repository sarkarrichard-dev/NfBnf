"""
Option-premium proxy for the CPR+EMA buying strategy.

No historical option chain exists in this project, so the premium path over a
trade is modelled with a zero-rate Black-Scholes (``math.erf``, ~1 screen of
code). That gives premium, delta and theta from one consistent formula, so a
20%-premium stop and a 1:2-in-premium target can be tracked bar by bar.

ponytail: BS proxy, not real marks. IV and assumed days-to-expiry are config
knobs (options_cpr.config) — calibrate them against the paper journal, and swap
this for real Dhan option LTP if premium-level accuracy ever matters.
"""

from __future__ import annotations

import math

_MINUTES_PER_YEAR = 365.0 * 375.0  # 375 trading minutes / session


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price_delta(
    spot: float, strike: float, t_years: float, iv: float, is_call: bool
) -> tuple[float, float]:
    """(premium, delta) for a European option, r = 0, no dividends."""
    if t_years <= 0.0 or iv <= 0.0 or spot <= 0.0 or strike <= 0.0:
        intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
        itm = (is_call and spot > strike) or (not is_call and spot < strike)
        return intrinsic, (1.0 if is_call else -1.0) if itm else 0.0
    vol = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * vol * vol) / vol
    d2 = d1 - vol
    if is_call:
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2), _norm_cdf(d1)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1), -_norm_cdf(-d1)


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * float(step)


def select_strike(spot: float, step: int, is_call: bool, mode: str) -> float:
    """ATM, or one step OTM when ``mode`` is 'OTM1'."""
    atm = atm_strike(spot, step)
    if str(mode).upper() != "OTM1":
        return atm
    return atm + step if is_call else atm - step


def premium_at(
    spot: float, strike: float, is_call: bool, iv: float, minutes_to_expiry: float
) -> float:
    t = max(0.0, minutes_to_expiry) / _MINUTES_PER_YEAR
    return bs_price_delta(spot, strike, t, iv, is_call)[0]


def strike_for_delta(
    spot: float, step: int, is_call: bool, iv: float, minutes_to_expiry: float, target_delta: float
) -> float:
    """Nearest strike (rounded to ``step``) whose |delta| is closest to ``target_delta``."""
    t = max(1e-9, minutes_to_expiry) / _MINUTES_PER_YEAR
    atm = atm_strike(spot, step)
    best, best_err = atm, 9.9
    for k in range(-12, 13):
        strike = atm + k * step
        if strike <= 0:
            continue
        d = abs(bs_price_delta(spot, strike, t, iv, is_call)[1])
        # for a call, delta falls as strike rises; only consider OTM strikes
        if is_call and strike < atm - step:
            continue
        if not is_call and strike > atm + step:
            continue
        if abs(d - target_delta) < best_err:
            best, best_err = strike, abs(d - target_delta)
    return best


if __name__ == "__main__":  # ponytail self-check
    # ATM call ~ 0.4 * sigma * S * sqrt(T) (Brenner-Subrahmanyam)
    S, T, IV = 24000.0, 2.0 / 365.0, 0.12
    px, dlt = bs_price_delta(S, S, T, IV, True)
    approx = 0.4 * IV * S * math.sqrt(T)
    assert abs(px - approx) / approx < 0.05, (px, approx)
    assert 0.45 < dlt < 0.55, dlt
    # deep ITM call ~ intrinsic, delta ~ 1
    px2, d2 = bs_price_delta(S + 2000, S, T, IV, True)
    assert px2 >= 2000 and d2 > 0.95, (px2, d2)
    # put-call: OTM put has negative delta, positive price
    pp, pd = bs_price_delta(S, S + 200, T, IV, False)
    assert pp > 0 and -1.0 < pd < 0.0, (pp, pd)
    # decays to intrinsic at expiry
    assert premium_at(S, S, True, IV, 0.0) == 0.0
    print("premium.py self-check ok")
