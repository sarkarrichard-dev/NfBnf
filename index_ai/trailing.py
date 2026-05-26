from __future__ import annotations

from typing import Any

from index_ai.config import RiskSettings
from index_ai.instruments import IndexInstrument, get_instrument


def _direction_for_action(action: str, transaction_type: str) -> int:
    """+1 = index up helps the position, -1 = index down helps."""
    action = action.upper()
    tx = transaction_type.upper()
    if action == "BUY_CALL":
        return 1 if tx == "BUY" else -1
    if action == "BUY_PUT":
        return -1 if tx == "BUY" else 1
    return 1


def init_trail_meta(
    *,
    entry_index_price: float,
    action: str,
    transaction_type: str,
    instrument: IndexInstrument,
) -> dict[str, Any]:
    """
    Two-phase trail:
    - Before activation: wide initial stop only (avoids 1-pt noise exits).
    - After activation: trail `trail_distance_points` behind favorable extreme.
    """
    direction = _direction_for_action(action, transaction_type)
    activation = instrument.trail_activation_points
    distance = instrument.trail_distance_points
    initial = instrument.initial_stop_points

    if direction > 0:
        initial_stop = entry_index_price - initial
    else:
        initial_stop = entry_index_price + initial

    return {
        "entry_index_price": entry_index_price,
        "anchor_index_price": entry_index_price,
        "stop_index_price": initial_stop,
        "trail_armed": False,
        "trail_activation_points": activation,
        "trail_distance_points": distance,
        "initial_stop_points": initial,
        "direction": direction,
        "instrument": instrument.key,
    }


def update_trail(meta: dict[str, Any], current_index_price: float) -> dict[str, Any]:
    direction = int(meta.get("direction") or 1)
    activation = float(meta.get("trail_activation_points") or 25.0)
    distance = float(meta.get("trail_distance_points") or 40.0)
    initial = float(meta.get("initial_stop_points") or 100.0)
    entry = float(meta.get("entry_index_price") or current_index_price)
    armed = bool(meta.get("trail_armed"))
    anchor = float(meta.get("anchor_index_price") or entry)

    favorable_move = (current_index_price - entry) * direction

    if not armed:
        if favorable_move >= activation:
            armed = True
            anchor = current_index_price
        else:
            if direction > 0:
                stop = entry - initial
                hit = current_index_price <= stop
            else:
                stop = entry + initial
                hit = current_index_price >= stop
            return {
                **meta,
                "trail_armed": False,
                "anchor_index_price": anchor,
                "stop_index_price": stop,
                "last_index_price": current_index_price,
                "favorable_move": round(favorable_move, 2),
                "hit": hit,
            }

    if direction > 0:
        if current_index_price > anchor:
            anchor = current_index_price
        stop = anchor - distance
        hit = current_index_price <= stop
    else:
        if current_index_price < anchor:
            anchor = current_index_price
        stop = anchor + distance
        hit = current_index_price >= stop

    return {
        **meta,
        "trail_armed": True,
        "anchor_index_price": anchor,
        "stop_index_price": stop,
        "last_index_price": current_index_price,
        "favorable_move": round(favorable_move, 2),
        "hit": hit,
    }


def evaluate_open_trade(
    trade: dict[str, Any],
    current_index_price: float,
    risk: RiskSettings,
) -> dict[str, Any]:
    signal = trade.get("signal") or {}
    option = trade.get("option") or {}
    action = str(trade.get("action") or signal.get("action") or "")
    tx = str(option.get("transaction_type") or "BUY")
    entry = float(signal.get("price") or current_index_price)
    instrument_key = str(trade.get("instrument") or option.get("instrument") or "NIFTY")
    instrument = get_instrument(instrument_key)

    meta = option.get("trail_meta")
    if not meta or "trail_armed" not in meta:
        meta = init_trail_meta(
            entry_index_price=entry,
            action=action,
            transaction_type=tx,
            instrument=instrument,
        )
    updated = update_trail(meta, current_index_price)
    armed = updated.get("trail_armed")
    if armed:
        dist = instrument.trail_distance_points
        exit_msg = (
            f"Trailing stop armed — {dist:g} index pts behind peak at {current_index_price:g}"
        )
    else:
        init_pts = instrument.initial_stop_points
        exit_msg = f"Initial stop ({init_pts:g} index pts) hit at {current_index_price:g}"

    return {
        "trade_id": trade.get("id"),
        "instrument": trade.get("instrument"),
        "action": action,
        "transaction_type": tx,
        "current_index_price": current_index_price,
        "trail": updated,
        "should_exit": bool(updated.get("hit")),
        "exit_reason": exit_msg if updated.get("hit") else None,
    }
