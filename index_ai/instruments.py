from __future__ import annotations

import logging
import os
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# NSE index F&O market lots (post Dec-2025 revision). Source: NSE/FAOP/70616.
NSE_MARKET_LOT: dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "SENSEX": 20,
}

# Pre-revision lots still common in old .env files — map to current NSE values.
_LEGACY_LOT_SIZE: dict[str, frozenset[int]] = {
    "NIFTY": frozenset({75}),
    "BANKNIFTY": frozenset({35}),
    "SENSEX": frozenset({10}),
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


def _buy_scalp_trail(points: float) -> dict[str, float]:
    """Option-buy stop/trail in INDEX points (Richard, 2026-09-24: "options
    buying is a quick scalping game"). The stop starts ``points`` from the
    entry and moves up one point for every point the index moves in the
    trade's favour -- a 1:1 trail from the first tick, no wide initial stop,
    no waiting to arm. NIFTY 25 and BANKNIFTY 55 are the middle of his 20-30 /
    50-60 ranges; SENSEX 80 is NIFTY's 25 scaled by index size (~3.2x), not
    his own number. Only the buy lane reads these (credit spreads have
    their own premium trail)."""
    return {
        "trail_activation_points": 0.0,
        "trail_distance_points": points,
        "initial_stop_points": points,
    }


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
            **_buy_scalp_trail(25.0),
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
            **_buy_scalp_trail(55.0),
        ),
        "SENSEX": IndexInstrument(
            key="SENSEX",
            label="SENSEX",
            underlying_security_id=_int_env("SENSEX_SECURITY_ID", 51),
            underlying_segment="IDX_I",
            instrument_type="INDEX",
            option_segment="BSE_FNO",
            strike_step=100,
            lot_size=market_lot_size("SENSEX"),
            **_buy_scalp_trail(80.0),
        ),
    }


def get_instrument(key: str) -> IndexInstrument:
    lookup = instruments()
    normalized = key.strip().upper()
    if normalized not in lookup:
        raise ValueError(f"Unknown index: {key}. Supported: {', '.join(lookup)}.")
    return lookup[normalized]


def index_paused(key: str) -> bool:
    """ENABLE_<KEY>=false in .env / feature toggles takes an index offline without
    touching its security id. Default on. Currently only SENSEX is exposed."""
    return os.getenv(f"ENABLE_{key.upper()}", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }


def configured_index_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key, inst in instruments().items()
        if inst.underlying_security_id is not None and not index_paused(key)
    )


def unconfigured_index_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key, inst in instruments().items()
        if inst.underlying_security_id is None or index_paused(key)
    )
