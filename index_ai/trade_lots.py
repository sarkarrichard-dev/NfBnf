"""Configurable lots per trade (NSE lot size × lots). Persisted in memory DB."""

from __future__ import annotations

import json
import threading
import os
from typing import Any

from index_ai.instruments import (
    IndexInstrument,
    configured_index_keys,
    get_instrument,
)
from index_ai.learning import connect, now_utc
from index_ai.risk_policy import HARDCODED_RISK

SETTINGS_KEY = "trade_lots"
MIN_LOTS_PER_TRADE = 1
MAX_LOTS_PER_TRADE = 10


def _clamp_lots(value: int) -> int:
    return max(MIN_LOTS_PER_TRADE, min(MAX_LOTS_PER_TRADE, int(value)))


def _default_lots_from_env() -> int:
    raw = os.getenv("LOTS_PER_TRADE", "").strip()
    if raw.isdigit():
        return _clamp_lots(int(raw))
    return int(HARDCODED_RISK.lots_per_trade)


def _load_stored_lots() -> int | None:
    with connect() as db:
        row = db.execute(
            "SELECT value_json FROM learned_settings WHERE key = ?",
            (SETTINGS_KEY,),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["value_json"])
        if isinstance(data, dict) and "lots_per_trade" in data:
            return _clamp_lots(int(data["lots_per_trade"]))
        if isinstance(data, int):
            return _clamp_lots(data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


def get_lots_per_trade() -> int:
    stored = _load_stored_lots()
    if stored is not None:
        return stored
    return _default_lots_from_env()


def set_lots_per_trade(lots: int) -> dict[str, Any]:
    value = _clamp_lots(lots)
    payload = {"lots_per_trade": value, "updated_at": now_utc()}
    with connect() as db:
        db.execute(
            """
            INSERT INTO learned_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (SETTINGS_KEY, json.dumps(payload), now_utc()),
        )
    return lots_settings_summary()


# read-modify-write: two fast clicks on "+" both read the same value and the
# second write discards the first, so the counter and the real lot size diverge.
# On a trading system that means orders sized differently from what is displayed.
_LOTS_LOCK = threading.Lock()


def adjust_lots_per_trade(delta: int) -> dict[str, Any]:
    """Relative change, serialized. Prefer set_lots_per_trade() from a UI — an
    absolute target is idempotent and cannot drift from what the user sees."""
    with _LOTS_LOCK:
        return set_lots_per_trade(get_lots_per_trade() + int(delta))


def order_quantity(instrument: IndexInstrument) -> int:
    """Units for a NEW order. Plan stamping and the pre-order quantity check
    both read this, so they always agree; the account risk manager drops it
    to 1 lot once half the day's live loss budget is gone."""
    from index_ai.risk_manager import india_lots

    return int(instrument.lot_size) * india_lots(get_lots_per_trade())


def stamp_option_quantities(option: dict[str, Any], instrument: IndexInstrument) -> dict[str, Any]:
    """Set parent and leg quantities for the current lots-per-trade setting."""
    qty = order_quantity(instrument)
    out = dict(option)
    out["quantity"] = qty
    legs = out.get("legs")
    if legs:
        out["legs"] = [{**dict(leg), "quantity": qty} for leg in legs]
    return out


def lots_settings_summary() -> dict[str, Any]:
    lots = get_lots_per_trade()
    per_index: dict[str, dict[str, int | str]] = {}
    for key in configured_index_keys():  # paused indices drop out of the UI qty line
        inst = get_instrument(key)
        qty = int(inst.lot_size) * lots
        per_index[key] = {
            "units_per_lot": int(inst.lot_size),
            "lots_per_trade": lots,
            "order_quantity": qty,
        }
    return {
        "lots_per_trade": lots,
        "min_lots": MIN_LOTS_PER_TRADE,
        "max_lots": MAX_LOTS_PER_TRADE,
        "per_index": per_index,
        "label": f"{lots} lot{'s' if lots != 1 else ''}",
    }
