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
    exchange: str = "NSE"  # SENSEX -> BSE

    # --- 15-minute trend filter ---
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21
    trend_st_period: int = 10
    trend_st_mult: float = 3.0
    trend_min_bars: int = 30  # total 15m bars (prev day + today) before the trend read is trusted

    # --- 5-minute entry trigger ---
    entry_mode: str = "ema_reclaim"  # "ema_reclaim" | "orb"
    entry_ema: int = 9
    entry_min_bars: int = 12
    max_extension_pct: float = 0.30  # skip entry if 5m close is >this% above/below the 5m EMA
    orb_minutes: int = 45  # opening-range window for entry_mode="orb"
    max_trades_per_session: int = 0  # 0 = unlimited; 1-2 = only the best setup(s) of the day

    # --- selectivity filters (all off / permissive by default) ---
    entry_window_end: time = time(15, 0)  # no NEW entries after this (afternoon = reversals)
    min_orb_range_pct: float = 0.0  # skip the day unless the opening range is >= this % wide
    require_5m_st_aligned: bool = False  # 5m Supertrend must also agree with the 15m trend
    st5_period: int = 10
    st5_mult: float = 2.0

    # --- risk, index points ---
    initial_stop_pts: float = 40.0
    trail_activate_pts: float = 30.0
    trail_pts: float = 22.0
    daily_stop_pts: float = 120.0  # stop trading the instrument for the day after this loss
    # phase 2 -- a tighter trail once the trade has run well past where phase 1
    # armed (Richard, 2026-09-15: every segment needs a real trailing-stop AND
    # a separate trailing-profit phase, not one trail doing both jobs). Must
    # have profit_trigger_pts > trail_activate_pts and profit_trail_pts <
    # trail_pts or phase 2 is a no-op. See index_ai/strategies/futures/
    # price_trail.py.
    profit_trigger_pts: float = 90.0
    profit_trail_pts: float = 8.0

    # --- session, IST ---
    entry_start: time = time(9, 45)
    entry_end: time = time(15, 0)
    square_off: time = time(15, 20)


def _lot(key: str) -> int:
    try:
        from index_ai.instruments import market_lot_size

        return market_lot_size(key)
    except Exception:
        return {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}.get(key.upper(), 65)


# risk (index points) is roughly proportional to each index's typical range.
# profit_trigger_pts / profit_trail_pts (phase 2, added 2026-09-15) follow the
# same ratios already implicit in the other three fields: trigger ~2.5x the
# phase-1 arm level (a meaningfully bigger move before the tighter floor
# engages), trail ~0.35x the phase-1 trail distance (locks in more once it
# does) -- a reasoned starting point in the same spirit as the original three,
# not independently re-measured; retune here if forward paper data says to.
_RISK: dict[str, dict[str, float]] = {
    "NIFTY": {
        "initial_stop_pts": 45.0,
        "trail_activate_pts": 45.0,
        "trail_pts": 80.0,
        "daily_stop_pts": 110.0,
        "profit_trigger_pts": 113.0,
        "profit_trail_pts": 28.0,
    },
    "BANKNIFTY": {
        "initial_stop_pts": 120.0,
        "trail_activate_pts": 120.0,
        "trail_pts": 200.0,
        "daily_stop_pts": 300.0,
        "profit_trigger_pts": 300.0,
        "profit_trail_pts": 70.0,
    },
    "SENSEX": {
        "initial_stop_pts": 220.0,
        "trail_activate_pts": 220.0,
        "trail_pts": 380.0,
        "daily_stop_pts": 550.0,
        "profit_trigger_pts": 550.0,
        "profit_trail_pts": 133.0,
    },
}


def config_for(instrument_key: str) -> FuturesConfig:
    key = str(instrument_key).upper()
    base = FuturesConfig(key=key, lot_size=_lot(key), exchange="BSE" if key == "SENSEX" else "NSE")
    return replace(base, **_RISK.get(key, {}))


def with_overrides(cfg: FuturesConfig, **kw: object) -> FuturesConfig:
    """Return a copy with the given fields replaced (ignores unknown keys)."""
    fields = {f for f in cfg.__dataclass_fields__}
    return replace(cfg, **{k: v for k, v in kw.items() if k in fields})
