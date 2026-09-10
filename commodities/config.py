"""Runtime knobs for the commodity lane. Read fresh each call so a saved .env
key takes effect without a restart (same as index / crypto config)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from commodities.instruments import BY_KEY, COMMODITIES
from index_ai.strategies.futures.config import FuturesConfig

COMMODITY_ENV_KEYS = (
    "ENABLE_COMMODITIES_PAPER",
    "COMMODITY_SYMBOLS",
    "COMMODITY_LOTS",
    "COMMODITY_MAX_CONCURRENT",
    "COMMODITY_MAX_OPEN_TOTAL",
    "COMMODITY_MAX_TRADES_PER_DAY",
)


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CommoditySettings:
    enabled: bool
    symbols: tuple[str, ...]
    lots: int
    max_concurrent: int          # open positions per instrument
    max_open_total: int          # portfolio cap across the section; 0 = unlimited
    max_trades_per_day: int      # per instrument; 0 = unlimited


def _symbols() -> tuple[str, ...]:
    raw = os.getenv("COMMODITY_SYMBOLS", "")
    picked = [x.strip().upper() for x in raw.split(",") if x.strip()]
    valid = [s for s in (picked or [c.key for c in COMMODITIES]) if s in BY_KEY]
    seen: list[str] = []
    for s in valid:
        if s not in seen:
            seen.append(s)
    return tuple(seen or [c.key for c in COMMODITIES])


def commodity_settings() -> CommoditySettings:
    return CommoditySettings(
        enabled=_b("ENABLE_COMMODITIES_PAPER", True),
        symbols=_symbols(),
        lots=max(1, _i("COMMODITY_LOTS", 1)),
        max_concurrent=max(1, _i("COMMODITY_MAX_CONCURRENT", 1)),
        max_open_total=max(0, _i("COMMODITY_MAX_OPEN_TOTAL", 3)),
        max_trades_per_day=max(0, _i("COMMODITY_MAX_TRADES_PER_DAY", 4)),
    )


def signal_config() -> FuturesConfig:
    """The directional signal knobs — reuse the index-futures engine (CPR + EMA
    9/21 + Supertrend 10,3). Risk is percent-based here (on `CommoditySpec`), so
    the point-based risk fields on FuturesConfig are ignored by this lane.
    `FUT_*` env overrides still apply (shared with the index futures lane)."""
    cfg = FuturesConfig(key="MCX", lot_size=1)
    ov: dict[str, object] = {}
    for field in cfg.__dataclass_fields__:
        env = os.getenv(f"FUT_{field.upper()}")
        if env is None:
            continue
        cur = getattr(cfg, field)
        try:
            ov[field] = env.lower() in {"1", "true", "yes"} if isinstance(cur, bool) else type(cur)(env)
        except Exception:
            pass
    from index_ai.strategies.futures.config import with_overrides

    return with_overrides(cfg, **ov) if ov else cfg


if __name__ == "__main__":  # self-check
    s = commodity_settings()
    assert s.symbols and all(k in BY_KEY for k in s.symbols)
    assert s.lots >= 1 and s.max_concurrent >= 1
    assert isinstance(signal_config(), FuturesConfig)
    print("commodities.config self-check ok -", s.symbols, "lots", s.lots)
