"""The tradeable MCX commodity set and its contract specs.

`CommoditySpec` is a **code constant** — the small set Richard picked (energy +
precious, mini/micro contracts to keep margin per lot low). The *derived* data —
the near-month contract's Dhan security id, its exact lot units and expiry — rolls
monthly and is resolved from the Dhan scrip master by
`scripts/fetch_commodity_universe.py`, cached to `memory/commodity_universe.json`,
and read back by `load_universe_meta()`.

`multiplier` is the rupees of P&L per 1.0 move in the quoted price, per lot
(CRUDEOILM is 10 barrels quoted ₹/bbl → 10; NATGASMINI 250 mmBtu → 250; GOLDM
100 g quoted ₹/10 g → 10; SILVERMIC 1 kg quoted ₹/kg → 1). Verify against a real
Dhan contract note before this ever goes near a live order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from index_ai.config import MEMORY_DIR
from index_ai.instruments import IndexInstrument

UNIVERSE_META_PATH = MEMORY_DIR / "commodity_universe.json"


@dataclass(frozen=True)
class CommoditySpec:
    key: str                 # our short key (also the journal `strategy`-peer tag)
    root: str                # MCX trading-symbol root, e.g. "CRUDEOILM"
    label: str
    multiplier: float        # ₹ P&L per 1.0 price move, per lot
    tick: float              # min price increment (₹)
    dst_session: bool        # evening close tracks US DST (23:55 in summer)
    initial_stop_pct: float  # % of entry price
    trail_activate_pct: float
    trail_pct: float
    daily_stop_pct: float    # stop trading this instrument for the day after this loss
    margin_pct: float        # rough SPAN+exposure as a fraction of notional (sizing only)


COMMODITIES: tuple[CommoditySpec, ...] = (
    CommoditySpec("CRUDEOILM", "CRUDEOILM", "Crude Oil Mini", 10.0, 1.0, True,
                  0.85, 0.6, 0.45, 2.2, 0.13),
    CommoditySpec("NATGASMINI", "NATGASMINI", "Natural Gas Mini", 250.0, 0.1, True,
                  1.2, 0.9, 0.7, 3.0, 0.15),
    CommoditySpec("GOLDM", "GOLDM", "Gold Mini", 10.0, 1.0, True,
                  0.45, 0.35, 0.25, 1.1, 0.09),
    CommoditySpec("SILVERMIC", "SILVERMIC", "Silver Micro", 1.0, 1.0, True,
                  0.8, 0.6, 0.45, 1.8, 0.12),
)

BY_KEY: dict[str, CommoditySpec] = {c.key: c for c in COMMODITIES}


def load_universe_meta(path: str | Path = UNIVERSE_META_PATH) -> dict[str, dict]:
    """``{key: {"security_id": int, "lot_units": int, "expiry": "YYYY-MM-DD",
    "trading_symbol": str}}`` from the fetch cache, or ``{}``."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def candle_instrument(spec: CommoditySpec, security_id: int) -> IndexInstrument:
    """An IndexInstrument-shaped object for the Dhan chart endpoints — they read
    only security_id / segment / instrument_type / key / label."""
    return IndexInstrument(
        key=spec.key,
        label=spec.label,
        underlying_security_id=int(security_id),
        underlying_segment="MCX_COMM",
        instrument_type="FUTCOM",
        option_segment="MCX_COMM",
        strike_step=1,
        lot_size=1,
        trail_activation_points=0.0,
        trail_distance_points=0.0,
        initial_stop_points=0.0,
    )


if __name__ == "__main__":  # self-check
    assert len(COMMODITIES) == len({c.key for c in COMMODITIES})
    for c in COMMODITIES:
        assert c.multiplier > 0 and c.tick > 0 and 0 < c.margin_pct < 1
    meta = load_universe_meta()
    resolved = [k for k in BY_KEY if k in meta]
    print(f"commodities.instruments: {len(COMMODITIES)} specs, {len(resolved)} resolved "
          f"({resolved or 'run scripts.fetch_commodity_universe'})")
