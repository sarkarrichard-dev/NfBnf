"""Rough option PnL proxy for backtests (no historical option chain)."""

from __future__ import annotations

import math

_BULLISH = frozenset({"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"})
_BEARISH = frozenset({"BUY_PUT", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_CALL"})
_CREDIT_RANGE = frozenset({"SELL_IRON_CONDOR"})
_CREDIT_DIR = frozenset({"SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_PUT", "SELL_ATM_CALL"})
_BUY = frozenset({"BUY_CALL", "BUY_PUT"})


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

    # Theta: credit structures earn decay; long premium bleeds.
    theta_per_hour = entry * 0.00015 * lots * (1.0 if act in _CREDIT_DIR | _CREDIT_RANGE else -1.0)
    theta_rupees = theta_per_hour * (minutes / 60.0)

    # Assumed credit collected (directional spreads / condor) or debit paid (buy).
    if act in _CREDIT_DIR | _CREDIT_RANGE:
        width_factor = 0.35 if act == "SELL_IRON_CONDOR" else 0.55
        credit_rupees = entry * 0.0012 * lots * width_factor
    elif act in _BUY:
        credit_rupees = -entry * 0.0018 * lots
    else:
        credit_rupees = 0.0

    # Directional loss from adverse spot move (spread-defined risk capped).
    adverse = 0.0
    if act in _BULLISH:
        adverse = max(0.0, -move) * delta
    elif act in _BEARISH:
        adverse = max(0.0, move) * delta
    elif act in _CREDIT_RANGE:
        adverse = abs(move) * delta * 0.65

    max_loss_pts = entry * 0.004 if act in _CREDIT_DIR else entry * 0.008
    adverse_pts = min(adverse, max_loss_pts)
    adverse_rupees = adverse_pts * lots

    favorable = 0.0
    if act in _BULLISH:
        favorable = max(0.0, move) * delta * 0.35
    elif act in _BEARISH:
        favorable = max(0.0, -move) * delta * 0.35

    gamma_boost = 0.0
    if act in _BUY:
        gamma_boost = move * delta * lots * (1.0 + min(1.0, abs(move) / max(entry * 0.003, 1.0)))

    iv_penalty = 0.0
    if act in _BUY and iv_pct > 0:
        iv_penalty = entry * 0.00005 * lots * math.sqrt(minutes / 60.0)

    pnl_rupees = credit_rupees + theta_rupees + favorable * lots - adverse_rupees + gamma_boost - iv_penalty

    if act in _CREDIT_DIR | _CREDIT_RANGE:
        max_profit = credit_rupees + theta_rupees * 1.5
        max_loss = -(credit_rupees * 1.8 + adverse_rupees)
        pnl_rupees = max(max_loss, min(max_profit, pnl_rupees))

    proxy_points = pnl_rupees / max(lots, 1) / max(delta, 0.1)
    return {
        "proxy_index_points": round(proxy_points, 2),
        "proxy_pnl_rupees": round(pnl_rupees, 2),
        "theta_rupees": round(theta_rupees, 2),
        "credit_or_debit_rupees": round(credit_rupees, 2),
        "delta_used": delta,
    }
