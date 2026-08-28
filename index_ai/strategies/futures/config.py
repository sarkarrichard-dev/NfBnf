"""Per-instrument config for the directional index-futures strategy.

One engine, three configs. Everything is in index points except lot_size.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time


@dataclass(frozen=True)
class FuturesConfig:
    key: str
    lot_size: int
    exchange: str = "NSE"          # SENSEX -> BSE

    # --- 15-minute trend filter ---
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21
    trend_st_period: int = 10
    trend_st_mult: float = 3.0
    trend_min_bars: int = 30       # total 15m bars (prev day + today) before the trend read is trusted

    # --- 5-minute entry trigger ---
    entry_mode: str = "ema_reclaim"   # "ema_reclaim" | "orb"
    entry_ema: int = 9
    entry_min_bars: int = 12
    max_extension_pct: float = 0.30    # skip entry if 5m close is >this% above/below the 5m EMA
    orb_minutes: int = 45             # opening-range window for entry_mode="orb"
    max_trades_per_session: int = 0    # 0 = unlimited; 1-2 = only the best setup(s) of the day

    # --- risk, index points ---
    initial_stop_pts: float = 40.0
    trail_activate_pts: float = 30.0
    trail_pts: float = 22.0
    daily_stop_pts: float = 120.0   # stop trading the instrument for the day after this loss

    # --- session, IST ---
    entry_start: time = time(9, 45)
    entry_end: time = time(15, 0)
    square_off: time = time(15, 20)


_BASE = FuturesConfig(key="NIFTY", lot_size=75)

PRESETS: dict[str, FuturesConfig] = {
    "NIFTY": _BASE,
    "BANKNIFTY": replace(
        _BASE, key="BANKNIFTY", lot_size=15,
        initial_stop_pts=130.0, trail_activate_pts=100.0, trail_pts=75.0, daily_stop_pts=380.0,
    ),
    "SENSEX": replace(
        _BASE, key="SENSEX", lot_size=10, exchange="BSE",
        initial_stop_pts=140.0, trail_activate_pts=110.0, trail_pts=80.0, daily_stop_pts=420.0,
    ),
}


def config_for(instrument_key: str) -> FuturesConfig:
    return PRESETS.get(str(instrument_key).upper(), _BASE)


def with_overrides(cfg: FuturesConfig, **kw: object) -> FuturesConfig:
    """Return a copy with the given fields replaced (ignores unknown keys)."""
    fields = {f for f in cfg.__dataclass_fields__}
    return replace(cfg, **{k: v for k, v in kw.items() if k in fields})
