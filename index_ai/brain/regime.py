"""
Market-regime read — which lane should be allowed to trade today.

Deterministic and measurable, not learned: with a few hundred trades there is
nowhere near enough data to *learn* a regime policy without overfitting, but the
mapping from regime to lane is well understood mechanically:

    TREND      -> directional lanes work, theta-selling into a run gets hurt
    RANGE      -> credit selling works, breakout buying churns
    HIGH_VOL   -> sell stands down (gaps blow through spread stops, spreads
                  widen); directional buying is allowed — range expansion is
                  what a long option is paid for
    QUIET      -> premiums too thin for selling to clear friction

Inputs are prior-day and opening-range measurements available before the first
entry, so the read is stable for the whole session rather than flip-flopping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

TREND, RANGE, HIGH_VOL, QUIET = "TREND", "RANGE", "HIGH_VOL", "QUIET"


@dataclass(frozen=True)
class RegimeRead:
    regime: str
    cpr_width_pct: float
    prev_range_pct: float
    open_range_pct: float
    gap_pct: float
    allow_buy: bool
    allow_sell: bool
    allow_futures: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# thresholds are % of price; tuned to Indian index behaviour, all overridable
HIGH_VOL_PREV_RANGE = 1.6
HIGH_VOL_GAP = 0.8
QUIET_PREV_RANGE = 0.45
NARROW_CPR = 0.30
WIDE_CPR = 0.50
TRENDY_OPEN_RANGE = 0.35


def classify(
    today5: pd.DataFrame,
    prev_day5: pd.DataFrame,
    *,
    cpr_width_pct: float,
    open_range_minutes: int = 45,
) -> RegimeRead:
    prev_hi, prev_lo = float(prev_day5["high"].max()), float(prev_day5["low"].min())
    prev_close = float(prev_day5["close"].iloc[-1])
    prev_range_pct = (prev_hi - prev_lo) / max(prev_close, 1.0) * 100.0

    if today5.empty:
        return RegimeRead(
            QUIET,
            cpr_width_pct,
            prev_range_pct,
            0.0,
            0.0,
            False,
            False,
            False,
            "no session data yet",
        )

    today_open = float(today5["open"].iloc[0])
    gap_pct = (today_open - prev_close) / max(prev_close, 1.0) * 100.0

    ts = pd.to_datetime(today5["datetime"])
    window = today5[ts <= ts.iloc[0] + pd.Timedelta(minutes=open_range_minutes)]
    if window.empty:
        window = today5
    or_hi, or_lo = float(window["high"].max()), float(window["low"].min())
    open_range_pct = (or_hi - or_lo) / max(today_open, 1.0) * 100.0

    if prev_range_pct >= HIGH_VOL_PREV_RANGE or abs(gap_pct) >= HIGH_VOL_GAP:
        return RegimeRead(
            HIGH_VOL,
            cpr_width_pct,
            prev_range_pct,
            open_range_pct,
            gap_pct,
            True,
            False,
            False,
            f"prior range {prev_range_pct:.2f}% / gap {gap_pct:+.2f}% — "
            "sell stands down, directional buying allowed (wider moves, tighter stop expected)",
        )
    if prev_range_pct <= QUIET_PREV_RANGE and open_range_pct < TRENDY_OPEN_RANGE * 0.6:
        return RegimeRead(
            QUIET,
            cpr_width_pct,
            prev_range_pct,
            open_range_pct,
            gap_pct,
            False,
            False,
            False,
            f"prior range {prev_range_pct:.2f}% — premiums too thin to clear friction",
        )
    if cpr_width_pct <= NARROW_CPR and open_range_pct >= TRENDY_OPEN_RANGE:
        return RegimeRead(
            TREND,
            cpr_width_pct,
            prev_range_pct,
            open_range_pct,
            gap_pct,
            True,
            True,
            True,
            f"narrow CPR {cpr_width_pct:.2f}% + wide open range {open_range_pct:.2f}% — trend day",
        )
    if cpr_width_pct >= WIDE_CPR or open_range_pct < TRENDY_OPEN_RANGE:
        return RegimeRead(
            RANGE,
            cpr_width_pct,
            prev_range_pct,
            open_range_pct,
            gap_pct,
            False,
            True,
            False,
            f"wide CPR {cpr_width_pct:.2f}% / tight open {open_range_pct:.2f}% — range day, sell only",
        )
    return RegimeRead(
        TREND,
        cpr_width_pct,
        prev_range_pct,
        open_range_pct,
        gap_pct,
        True,
        True,
        True,
        f"CPR {cpr_width_pct:.2f}%, open range {open_range_pct:.2f}% — normal trend bias",
    )


def allows(read: RegimeRead, lane: str) -> bool:
    return {"buy": read.allow_buy, "sell": read.allow_sell, "futures": read.allow_futures}.get(
        str(lane).lower(), True
    )


if __name__ == "__main__":  # ponytail self-check

    def day(o, h, low, c, n=60, start="2026-08-28 09:15"):
        return pd.DataFrame(
            {
                "datetime": pd.date_range(start, periods=n, freq="5min"),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 0.0,
            }
        )

    calm = day(24000, 24060, 23960, 24010)
    wild = day(24000, 24400, 23700, 24100)
    today_trendy = day(24010, 24120, 23990, 24100, start="2026-08-29 09:15")

    r = classify(today_trendy, wild, cpr_width_pct=0.2)
    assert r.regime == HIGH_VOL and not r.allow_sell and r.allow_buy, r

    r = classify(today_trendy, calm, cpr_width_pct=0.2)
    assert r.regime == TREND and r.allow_buy and r.allow_futures, r

    # normal prior range, tight opening range, wide CPR -> range day (sell only)
    normal = day(24000, 24140, 23880, 24010)
    flat_today = day(24010, 24030, 23995, 24010, start="2026-08-29 09:15")
    r = classify(flat_today, normal, cpr_width_pct=0.7)
    assert r.regime == RANGE and r.allow_sell and not r.allow_buy, r

    # genuinely dead prior session -> QUIET, nothing trades
    r = classify(flat_today, calm, cpr_width_pct=0.7)
    assert r.regime == QUIET and not r.allow_sell, r

    gapped = day(24300, 24350, 24250, 24300, start="2026-08-29 09:15")
    r = classify(gapped, calm, cpr_width_pct=0.2)
    assert r.regime == HIGH_VOL, r  # +1.2% gap

    assert allows(r, "sell") is False and allows(r, "unknown_lane") is True
    print("regime.py self-check ok")
