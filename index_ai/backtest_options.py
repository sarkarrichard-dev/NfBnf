"""Rough option PnL proxy for backtests (no historical option chain)."""

from __future__ import annotations

from index_ai.charges import half_spread_points, leg_charge_rupees

_BULLISH = frozenset({"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"})
_BEARISH = frozenset({"BUY_PUT", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_CALL"})
_CREDIT_RANGE = frozenset({"SELL_IRON_CONDOR"})
_CREDIT_DIR = frozenset({"SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_PUT", "SELL_ATM_CALL"})
_BUY = frozenset({"BUY_CALL", "BUY_PUT"})


def _legs_for_action(action: str) -> int:
    act = str(action or "").upper()
    if act == "SELL_IRON_CONDOR":
        return 4
    if act in {"SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD"}:
        return 2
    return 1


def _delta_for_action(action: str) -> float:
    act = str(action or "").upper()
    if act in {"SELL_ATM_PUT", "SELL_ATM_CALL"}:
        return 0.45
    if act in {"SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD"}:
        return 0.28
    if act == "SELL_IRON_CONDOR":
        return 0.12
    if act in {"BUY_CALL", "BUY_PUT"}:
        return 0.50
    return 0.30


def estimate_option_pnl_rupees(
    action: str,
    entry_spot: float,
    exit_spot: float,
    *,
    lot_size: int,
    hold_minutes: float = 30.0,
    iv_pct: float = 14.0,
    slippage_bps: float = 10.0,
    fees_per_leg_side_rupees: float = 20.0,
    instrument_key: str = "NIFTY",
) -> dict[str, float]:
    """
    Estimate spread / premium PnL from spot path + simplified Greeks.

    Not a substitute for real option marks — calibrate against paper journal.
    """
    act = str(action or "").upper()
    entry = float(entry_spot)
    exit_px = float(exit_spot)
    move = exit_px - entry
    lots = max(1, int(lot_size))
    delta = _delta_for_action(act)
    minutes = max(5.0, float(hold_minutes))
    hours = minutes / 60.0

    if act in _BUY:
        # Long premium P&L = change in the option's price, not a sunk debit.
        #   d(premium) ~= delta*signed_move + 0.5*gamma*move^2 - theta*time
        direction = 1.0 if act == "BUY_CALL" else -1.0
        signed_move = move * direction
        gamma_term = 0.5 * (signed_move**2) / max(entry * 0.004, 1.0)   # convexity, always >= 0
        theta_decay = entry * 0.00035 * hours                          # weekly-ATM bleed / hr
        per_unit = signed_move * delta + gamma_term - theta_decay
        gross_pnl_rupees = per_unit * lots
        theta_rupees = -theta_decay * lots
        credit_rupees = -(entry * 0.005 * lots)                        # notional premium, report only
    else:
        # Credit structures: keep the collected premium unless the spot moves
        # against the short strike, then bleed toward the defined max loss.
        theta_rupees = entry * 0.00015 * lots * hours
        width_factor = 0.35 if act == "SELL_IRON_CONDOR" else 0.55
        credit_rupees = entry * 0.0012 * lots * width_factor

        adverse_move = 0.0
        if act in _BULLISH:
            adverse_move = max(0.0, -move)
        elif act in _BEARISH:
            adverse_move = max(0.0, move)
        elif act in _CREDIT_RANGE:
            adverse_move = abs(move)
        # Short strike sits ~1% OTM. Inside that it's a slow delta bleed; once the
        # spot breaches it, losses ramp fast toward the defined wing width.
        breach = entry * 0.010
        max_loss_rupees = (entry * (0.006 if act in _CREDIT_DIR else 0.012)) * lots
        if adverse_move <= breach:
            adverse_rupees = adverse_move * delta * lots * 1.4
        else:
            past_frac = min(1.0, (adverse_move - breach) / max(entry * 0.004, 1.0))  # wings ~0.4%
            adverse_rupees = min(
                max_loss_rupees,
                breach * delta * lots * 1.4 + past_frac * max_loss_rupees,
            )

        favorable = 0.0
        if act in _BULLISH:
            favorable = max(0.0, move) * delta * 0.15
        elif act in _BEARISH:
            favorable = max(0.0, -move) * delta * 0.15

        gross_pnl_rupees = credit_rupees + theta_rupees + favorable * lots - adverse_rupees
        max_profit = credit_rupees + theta_rupees
        gross_pnl_rupees = max(-max_loss_rupees, min(max_profit, gross_pnl_rupees))

    # Historical option quotes are unavailable in this replay, so deduct a
    # transparent friction estimate: real Indian F&O statutory + broker charges
    # (index_ai.charges) on an estimated per-leg premium, plus half-spread
    # slippage on entry and exit. Falls back to the legacy bps model if the
    # charges import is unavailable.
    legs = _legs_for_action(act)
    premium_per_leg = max(entry * 0.004, abs(credit_rupees) / max(legs, 1) / max(lots, 1))
    try:
        charge_one_leg = leg_charge_rupees(
            premium_per_leg, lots, "SELL", exchange="BSE" if instrument_key.upper() == "SENSEX" else "NSE"
        ) + leg_charge_rupees(
            premium_per_leg, lots, "BUY", exchange="BSE" if instrument_key.upper() == "SENSEX" else "NSE"
        )
        fees_rupees = charge_one_leg * legs
        slippage_rupees = half_spread_points(instrument_key) * lots * legs * 2
    except Exception:
        premium_notional = max(abs(credit_rupees), entry * 0.0018 * lots)
        slippage_rupees = premium_notional * max(0.0, float(slippage_bps)) * 2 / 10_000
        fees_rupees = max(0.0, float(fees_per_leg_side_rupees)) * legs * 2
    estimated_friction_rupees = slippage_rupees + fees_rupees
    pnl_rupees = gross_pnl_rupees - estimated_friction_rupees

    proxy_points = pnl_rupees / max(lots, 1) / max(delta, 0.1)
    return {
        "proxy_index_points": round(proxy_points, 2),
        "proxy_pnl_rupees": round(pnl_rupees, 2),
        "gross_proxy_pnl_rupees": round(gross_pnl_rupees, 2),
        "estimated_friction_rupees": round(estimated_friction_rupees, 2),
        "estimated_slippage_rupees": round(slippage_rupees, 2),
        "estimated_fees_rupees": round(fees_rupees, 2),
        "theta_rupees": round(theta_rupees, 2),
        "credit_or_debit_rupees": round(credit_rupees, 2),
        "delta_used": delta,
    }
