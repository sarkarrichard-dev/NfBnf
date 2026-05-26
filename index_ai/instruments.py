from __future__ import annotations

import logging
import os
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# NSE index F&O market lots (post Dec-2025 revision). Source: NSE/FAOP/70616.
NSE_MARKET_LOT: dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
}

# Pre-revision lots still common in old .env files — map to current NSE values.
_LEGACY_LOT_SIZE: dict[str, frozenset[int]] = {
    "NIFTY": frozenset({75}),
    "BANKNIFTY": frozenset({35}),
}


@dataclass(frozen=True)
class IndexInstrument:
    key: str
    label: str
    underlying_security_id: int | None
    underlying_segment: str
    instrument_type: str
    option_segment: str
    strike_step: int
    lot_size: int
    # Trailing: wide initial stop, then arm trail only after favorable index move
    trail_activation_points: float
    trail_distance_points: float
    initial_stop_points: float


def _int_env(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def market_lot_size(index_key: str) -> int:
    """Current NSE lot for index options; .env may override unless it is a known legacy value."""
    key = index_key.strip().upper()
    canonical = NSE_MARKET_LOT.get(key)
    if canonical is None:
        raise ValueError(f"Unknown index for lot size: {index_key}")
    env_name = f"{key}_LOT_SIZE"
    override = _int_env(env_name, None)
    if override is None:
        return canonical
    legacy = _LEGACY_LOT_SIZE.get(key, frozenset())
    if override in legacy:
        _log.warning(
            "%s=%s is the pre-Dec-2025 NSE lot; using current market lot %s. "
            "Update .env to %s=%s to silence this.",
            env_name,
            override,
            canonical,
            env_name,
            canonical,
        )
        return canonical
    if override != canonical:
        _log.info("Using %s=%s from .env (NSE default is %s).", env_name, override, canonical)
    return override


def instruments() -> dict[str, IndexInstrument]:
    return {
        "NIFTY": IndexInstrument(
            key="NIFTY",
            label="NIFTY",
            underlying_security_id=_int_env("NIFTY_SECURITY_ID", 13),
            underlying_segment="IDX_I",
            instrument_type="INDEX",
            option_segment="NSE_FNO",
            strike_step=50,
            lot_size=market_lot_size("NIFTY"),
            trail_activation_points=25.0,
            trail_distance_points=40.0,
            initial_stop_points=100.0,
        ),
        "BANKNIFTY": IndexInstrument(
            key="BANKNIFTY",
            label="BANK NIFTY",
            underlying_security_id=_int_env("BANKNIFTY_SECURITY_ID", 25),
            underlying_segment="IDX_I",
            instrument_type="INDEX",
            option_segment="NSE_FNO",
            strike_step=100,
            lot_size=market_lot_size("BANKNIFTY"),
            trail_activation_points=50.0,
            trail_distance_points=80.0,
            initial_stop_points=200.0,
        ),
    }


def get_instrument(key: str) -> IndexInstrument:
    lookup = instruments()
    normalized = key.strip().upper()
    if normalized not in lookup:
        raise ValueError("Only NIFTY and BANKNIFTY are supported.")
    return lookup[normalized]


def configured_index_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key, inst in instruments().items()
        if inst.underlying_security_id is not None
    )


def unconfigured_index_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key, inst in instruments().items()
        if inst.underlying_security_id is None
    )
