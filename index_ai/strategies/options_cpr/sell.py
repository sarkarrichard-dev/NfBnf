"""
Directional wide-credit-spread selling (spec: directional selling, not range).

Same CPR + EMA + 15m-Supertrend trend read as the buy lane, opposite structure:

    uptrend   -> SELL_BULL_PUT_SPREAD  (short a ~0.30-delta put, long a 3-5% OTM put)
    downtrend -> SELL_BEAR_CALL_SPREAD (mirror)

Theta works *for* the position. Exits: capture X% of the entry credit, the
mark-to-close debit reaches Y x credit (stop), spot closes back through the CPR
line, the 15m trend flips, or 15:10 square-off. The far wing is margin relief +
a catastrophe cap, not the working stop — intraday the position is stopped at a
small multiple of credit long before max loss.

Premium path is the same Black-Scholes proxy as ``premium.py`` — signal stats
are meaningful, rupee P&L is indicative.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.charges import leg_charge_rupees
from index_ai.strategies.options_cpr.backtest import _align15
from index_ai.strategies.options_cpr.config import OptionsCprConfig, config_for
from index_ai.strategies.options_cpr.engine import (
    add_indicators,
    cpr_context,
    entry_features,
    evaluate_entry,
)
from index_ai.strategies.options_cpr.premium import bs_price_delta, strike_for_delta

_MINS = 375.0
_MINUTES_PER_YEAR = 365.0 * 375.0


def _spread_mark(
    spot: float, short_k: float, long_k: float | None, is_put: bool, iv: float, mte: float
) -> float:
    """Cost to close the position now (debit), premium points. Entry credit - this = P&L.

    ``long_k`` None => naked single short leg.
    """
    t = max(0.0, mte) / _MINUTES_PER_YEAR
    is_call_leg = not is_put
    short_px = bs_price_delta(spot, short_k, t, iv, is_call_leg)[0]
    long_px = bs_price_delta(spot, long_k, t, iv, is_call_leg)[0] if long_k is not None else 0.0
    return short_px - long_px


def _build_spread(
    spot: float, cfg: OptionsCprConfig, is_put: bool, mte: float
) -> dict[str, Any]:
    """Short leg by delta + a far-OTM long wing, wing tightened inward until the
    defined-risk max loss fits ``sell_margin_budget_rupees``.
    """
    t = max(1e-9, mte) / _MINUTES_PER_YEAR
    step, iv, call = cfg.strike_step, cfg.iv, not is_put
    short_k = strike_for_delta(spot, step, call, iv, mte, cfg.sell_short_delta)
    short_px = bs_price_delta(spot, short_k, t, iv, call)[0]
    if cfg.sell_naked:
        return {"short_k": short_k, "long_k": None, "short_px": short_px, "long_px": 0.0,
                "credit": short_px, "max_loss_rupees": None}

    want = spot * (1.0 - cfg.sell_wing_pct) if is_put else spot * (1.0 + cfg.sell_wing_pct)
    long_k = round(want / step) * step
    long_k = min(long_k, short_k - step) if is_put else max(long_k, short_k + step)
    long_px = credit = max_loss = 0.0
    for _ in range(40):
        long_px = bs_price_delta(spot, long_k, t, iv, call)[0]
        credit = short_px - long_px
        width = abs(short_k - long_k)
        max_loss = (width - credit) * cfg.lot_size
        if max_loss <= cfg.sell_margin_budget_rupees or width <= step:
            break
        long_k += step if is_put else -step         # pull the wing one strike closer to the short
    return {"short_k": short_k, "long_k": long_k, "short_px": short_px, "long_px": long_px,
            "credit": credit, "max_loss_rupees": round(max_loss, 2)}


def _sell_friction(short_px: float, long_px: float, lot: int, cfg: OptionsCprConfig) -> float:
    """Round-trip cost of the structure: real charges per leg + bid-ask on each leg.

    Near-ATM and far-OTM legs are priced from *separate* measured buckets. Live
    quotes show the wing is the **wider** book in absolute points (NIFTY: ~0.20pt
    near vs ~0.60pt wing), which is the opposite of the intuition that a cheap
    option must be cheap to cross — the wing costs pennies but you give up a much
    larger fraction of it on every fill.
    """
    from index_ai.market_context.spread_calib import bucket_half_spreads

    near_hs, wing_hs = bucket_half_spreads(cfg.key)
    f = (
        leg_charge_rupees(max(short_px, 2.0), lot, "SELL", exchange=cfg.exchange)
        + leg_charge_rupees(max(short_px, 2.0), lot, "BUY", exchange=cfg.exchange)
        + near_hs * lot * 2
    )
    if long_px > 0:
        f += (
            leg_charge_rupees(max(long_px, 1.0), lot, "BUY", exchange=cfg.exchange)
            + leg_charge_rupees(max(long_px, 1.0), lot, "SELL", exchange=cfg.exchange)
            + wing_hs * lot * 2
        )
    return f


def replay_sell_session(
    bars5_prev_tail: pd.DataFrame,
    bars5_today: pd.DataFrame,
    bars15_prev: pd.DataFrame,
    bars15_today: pd.DataFrame,
    prev_day_ohlc: pd.DataFrame,
    cfg: OptionsCprConfig,
    *,
    require_15m_alignment: bool = True,
) -> list[dict[str, Any]]:
    cpr = cpr_context(prev_day_ohlc, cfg)
    n_tail = len(bars5_prev_tail)
    df = add_indicators(pd.concat([bars5_prev_tail, bars5_today], ignore_index=True), cfg)
    align15 = _align15(bars15_prev, bars15_today, cfg) if require_15m_alignment else (lambda _ts: 0)

    ts_all = pd.to_datetime(df["datetime"])
    open_ts = ts_all.iloc[n_tail] if len(df) > n_tail else None
    expiry_total = cfg.assumed_days_to_expiry * _MINS

    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    consec_losses = trades_today = 0
    daily_pnl = 0.0
    kill = False
    loss_cap = (cfg.daily_loss_cap_rupees or cfg.max_daily_loss_pct / 100.0 * cfg.capital)

    def mte(ts: pd.Timestamp) -> float:
        return max(0.0, expiry_total - (ts - open_ts).total_seconds() / 60.0)

    def close_pos(debit: float, reason: str, ts: pd.Timestamp, spot: float) -> None:
        nonlocal pos, consec_losses, daily_pnl, kill
        gross = (pos["entry_credit"] - debit) * cfg.lot_size
        fric = _sell_friction(pos["short_px"], pos["long_px"], cfg.lot_size, cfg)
        net = gross - fric
        daily_pnl += net
        trades.append({
            "instrument": cfg.key, "lane": "sell", "structure": pos["structure"],
            "reason": reason, "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
            "entry_spot": round(pos["entry_spot"], 2), "exit_spot": round(spot, 2),
            "short_strike": pos["short_k"], "long_strike": pos["long_k"],
            "entry_credit": round(pos["entry_credit"], 2), "exit_debit": round(debit, 2),
            "wing_pts": round(pos["wing_pts"], 1), "max_loss_rupees": pos["max_loss_rupees"],
            "qty": cfg.lot_size,
            "gross_rupees": round(gross, 2), "friction_rupees": round(fric, 2),
            "net_rupees": round(net, 2), "features": pos["features"],
        })
        if net > 0:
            consec_losses = 0
        else:
            consec_losses += 1
        if consec_losses >= cfg.max_consecutive_losses:
            kill = True
        pos = None

    for i in range(n_tail, len(df)):
        row = df.iloc[i]
        ts = ts_all.iloc[i]
        o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))

        if pos is not None:
            is_put = pos["structure"] in ("SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT")
            adverse_spot = low if is_put else h            # short puts hurt by a drop
            m = mte(ts)
            debit_now = _spread_mark(c, pos["short_k"], pos["long_k"], is_put, cfg.iv, m)
            debit_adverse = _spread_mark(adverse_spot, pos["short_k"], pos["long_k"], is_put, cfg.iv, m)

            broke_struct = (c < cpr.tc) if is_put else (c > cpr.bc)
            d15 = align15(ts)
            trend_flip = d15 != 0 and d15 != (1 if is_put else -1)

            if debit_adverse >= cfg.sell_stop_credit_mult * pos["entry_credit"]:
                close_pos(cfg.sell_stop_credit_mult * pos["entry_credit"], "spread_stop", ts, adverse_spot)
            elif debit_now <= (1.0 - cfg.sell_credit_capture_target) * pos["entry_credit"]:
                close_pos(debit_now, "credit_capture", ts, c)
            elif broke_struct:
                close_pos(debit_now, "structural_sl", ts, c)
            elif trend_flip:
                close_pos(debit_now, "trend_flip_15m", ts, c)
            elif ts.time() >= cfg.square_off_time:
                close_pos(debit_now, "square_off", ts, c)
            continue

        if kill or trades_today >= cfg.max_trades_per_day:
            continue
        if loss_cap > 0 and daily_pnl <= -loss_cap:
            continue
        if not (cfg.first_entry_time <= ts.time() <= cfg.last_entry_time):
            continue
        side, _why = evaluate_entry(df, i, cpr, cfg)
        if side is None:
            continue
        bullish = side == "CE"
        d15 = align15(ts)
        if d15 != 0 and d15 != (1 if bullish else -1):
            continue

        m = mte(ts)
        is_put = bullish
        naked = cfg.sell_naked
        structure = ("SELL_ATM_PUT" if is_put else "SELL_ATM_CALL") if naked else (
            "SELL_BULL_PUT_SPREAD" if bullish else "SELL_BEAR_CALL_SPREAD")
        sp = _build_spread(c, cfg, is_put, m)
        if sp["credit"] < cfg.sell_min_credit_pts:
            continue
        if sp["max_loss_rupees"] is not None and sp["max_loss_rupees"] > cfg.sell_margin_budget_rupees * 1.05:
            continue  # even the tightest wing can't fit the margin budget

        pos = {
            "structure": structure, "entry_spot": c, "entry_time": ts,
            "short_k": sp["short_k"], "long_k": sp["long_k"], "entry_credit": sp["credit"],
            "short_px": sp["short_px"], "long_px": sp["long_px"],
            "wing_pts": abs(sp["short_k"] - sp["long_k"]) if sp["long_k"] is not None else 0.0,
            "max_loss_rupees": sp["max_loss_rupees"],
            "features": entry_features(df, i, cpr, prev_day_ohlc),
        }
        trades_today += 1

    if pos is not None:
        is_put = pos["structure"] in ("SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT")
        last_c = float(df.iloc[-1]["close"])
        close_pos(_spread_mark(last_c, pos["short_k"], pos["long_k"], is_put, cfg.iv, mte(ts_all.iloc[-1])),
                  "session_end", ts_all.iloc[-1], last_c)
    return trades


def run_sell(
    instrument_key: str,
    bars5: pd.DataFrame,
    bars15: pd.DataFrame,
    *,
    cfg: OptionsCprConfig | None = None,
    sessions: int = 0,
    require_15m_alignment: bool = True,
) -> list[dict[str, Any]]:
    cfg = cfg or config_for(instrument_key)
    b5, b15 = bars5.copy(), bars15.copy()
    b5["_d"] = pd.to_datetime(b5["datetime"]).dt.date
    b15["_d"] = pd.to_datetime(b15["datetime"]).dt.date
    days = sorted(set(b5["_d"]))
    if sessions:
        days = days[-sessions:]
    out: list[dict[str, Any]] = []
    for idx in range(1, len(days)):
        d, prev = days[idx], days[idx - 1]
        today5 = b5[b5["_d"] == d].drop(columns="_d").reset_index(drop=True)
        prev5 = b5[b5["_d"] == prev].drop(columns="_d").reset_index(drop=True)
        today15 = b15[b15["_d"] == d].drop(columns="_d").reset_index(drop=True)
        prev15 = b15[b15["_d"] == prev].drop(columns="_d").reset_index(drop=True)
        if today5.empty or prev5.empty or prev15.empty:
            continue
        tail = prev5.tail(cfg.warmup_bars + 5)
        for t in replay_sell_session(tail, today5, prev15, today15, prev5, cfg,
                                     require_15m_alignment=require_15m_alignment):
            t["session"] = str(d)
            out.append(t)
    return out


if __name__ == "__main__":  # ponytail self-check
    import numpy as np

    cfg = config_for("NIFTY")
    prev = pd.DataFrame({
        "datetime": pd.date_range("2026-01-01 09:15", periods=26, freq="15min"),
        "open": 24000.0, "high": 24050.0, "low": 23950.0, "close": 24000.0, "volume": 0.0,
    })
    cpr = cpr_context(prev, cfg)
    # clean uptrend breakout above TC -> expect a bull-put spread
    closes = list(np.full(25, cpr.tc - 5)) + list(np.linspace(cpr.tc - 5, cpr.tc + 60, 30))
    t5 = pd.DataFrame({
        "datetime": pd.date_range("2026-01-02 09:15", periods=len(closes), freq="5min"),
        "open": closes, "high": [c + 5 for c in closes], "low": [c - 5 for c in closes],
        "close": closes, "volume": 0.0,
    })
    p15 = prev.assign(datetime=pd.date_range("2026-01-01 09:15", periods=26, freq="15min"))
    t15 = pd.DataFrame({
        "datetime": pd.date_range("2026-01-02 09:15", periods=26, freq="15min"),
        "open": 24100.0, "high": 24160.0, "low": 24050.0, "close": 24140.0, "volume": 0.0,
    })
    trades = replay_sell_session(t5.head(0).reindex(columns=t5.columns), t5, p15, t15, prev, cfg,
                                 require_15m_alignment=False)
    assert isinstance(trades, list)
    if trades:
        assert trades[0]["structure"] in ("SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD")
        assert trades[0]["entry_credit"] > 0 and trades[0]["long_strike"] < trades[0]["short_strike"] \
            if trades[0]["structure"] == "SELL_BULL_PUT_SPREAD" else trades[0]["long_strike"] > trades[0]["short_strike"]
    print(f"sell.py self-check ok — {len(trades)} spread trade(s)")
