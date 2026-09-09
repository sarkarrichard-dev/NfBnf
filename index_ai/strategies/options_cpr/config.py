"""Per-instrument config for the CPR + EMA option-buying strategy (Section 10 of the spec).

One engine, three configs. Rupee amounts are rupees; everything else is either
an index-point count, a fraction, or an IST ``datetime.time``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time


@dataclass(frozen=True)
class OptionsCprConfig:
    key: str
    lot_size: int
    strike_step: int
    exchange: str = "NSE"  # SENSEX -> BSE

    # --- account / risk (Section 4, 8) ---
    capital: float = 140_000.0
    max_loss_pct_of_utilized_capital: float = 5.0  # per-trade hard cap
    risk_reward_ratio: float = 2.0  # 1:2 fixed
    max_daily_loss_pct: float = 5.0  # of capital deployed that day
    daily_loss_cap_rupees: float = 0.0  # >0 overrides the pct circuit breaker
    max_consecutive_losses: int = 3  # kill switch
    max_trades_per_day: int = 3

    # --- indicators (Section 1) ---
    ema_fast: int = 9
    ema_slow: int = 21
    atr_period: int = 14
    volume_lookback: int = 10
    warmup_bars: int = 21  # 5m bars (prev-day tail) before signals are trusted

    # --- 15m trend / swing S&R for the directional-sell lane ---
    st_period: int = 10
    st_multiplier: float = 3.0
    trend15_swing_lookback: int = 6  # 15m bars bounding the swing high/low

    # --- CPR width class (Section 1, 2) ---
    cpr_narrow_threshold_pct: float = 0.3
    cpr_wide_threshold_pct: float = 0.5
    wide_cpr_confirm_bars: int = 2  # consecutive closes required on a WIDE-CPR day
    whipsaw_lookback_bars: int = 3  # skip if price crossed the CPR line and reversed within N bars

    # --- stops / targets (Section 5, 6, 7) ---
    initial_sl_premium_pct: float = 20.0  # fallback premium stop
    trail_stage1_trigger_r: float = 1.0  # -> breakeven
    trail_stage2_trigger_r: float = 1.5  # -> lock +0.5R
    trail_stage3_trigger_r: float = 2.0  # -> switch to ATR / EMA trail
    atr_multiplier: float = 1.5
    partial_book_fraction: float = 0.5  # book this much of the position at target_1

    # --- strike / premium model ---
    strike_selection: str = "ATM"  # "ATM" | "OTM1"
    iv: float = 0.13  # annualised, for the BS premium proxy
    assumed_days_to_expiry: float = 3.0  # fixed DTE for the proxy (weekly ~ 3, monthly ~ 8)

    # --- directional-sell lane (hedged credit spread) ---
    # Always long a far-OTM wing: converts the naked short into defined risk, so the
    # broker margins the spread (~ max loss) not SPAN+exposure — the trade fits a
    # small account. The wing is sized to the widest distance whose max loss still
    # fits ``sell_margin_budget_rupees``; a cheaper (further-OTM) wing keeps more
    # credit but widens max loss, so there is a sweet spot, not "as far as possible".
    sell_short_delta: float = 0.30  # target delta of the short leg (near-OTM)
    sell_wing_pct: float = 0.04  # desired long-wing distance from spot, fraction
    sell_margin_budget_rupees: float = 45000.0  # cap on defined-risk max loss (~ margin) per trade
    sell_credit_capture_target: float = (
        0.50  # exit when this fraction of the entry credit is decayed
    )
    sell_stop_credit_mult: float = 1.6  # exit when mark-to-close debit >= this x entry credit
    sell_min_credit_pts: float = 5.0  # skip if the modelled net credit is thinner than this
    sell_naked: bool = False  # True = single short leg, no wing (not deployable at small capital)

    # --- session, IST (Section 2) ---
    market_open: time = time(9, 15)
    first_entry_time: time = time(9, 20)
    last_entry_time: time = time(15, 0)
    square_off_time: time = time(15, 10)


def _lot(key: str) -> int:
    try:
        from index_ai.instruments import market_lot_size

        return market_lot_size(key)
    except Exception:
        return {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}.get(key.upper(), 65)


# per-index: strike step + a rough intraday ATM IV + typical days-to-expiry traded
_PRESET: dict[str, dict[str, object]] = {
    "NIFTY": {"strike_step": 50, "iv": 0.12, "assumed_days_to_expiry": 3.0},
    "BANKNIFTY": {"strike_step": 100, "iv": 0.15, "assumed_days_to_expiry": 8.0},
    "SENSEX": {"strike_step": 100, "iv": 0.13, "assumed_days_to_expiry": 3.0},
}


def config_for(instrument_key: str) -> OptionsCprConfig:
    key = str(instrument_key).upper()
    preset = _PRESET.get(key, {"strike_step": 50, "iv": 0.13, "assumed_days_to_expiry": 3.0})
    return OptionsCprConfig(
        key=key,
        lot_size=_lot(key),
        exchange="BSE" if key == "SENSEX" else "NSE",
        **preset,  # type: ignore[arg-type]
    )


def with_overrides(cfg: OptionsCprConfig, **kw: object) -> OptionsCprConfig:
    """Copy with the given known fields replaced (unknown keys ignored)."""
    fields = set(cfg.__dataclass_fields__)
    return replace(cfg, **{k: v for k, v in kw.items() if k in fields})
