"""
Backtest the "US-session-skip" short strangle spec'd in a YouTube video
(https://www.youtube.com/watch?v=Mt0uebHIu7s, request logged 2026-09-29):

    Entry  ~22:45 IST (after the 18:00-22:30 IST US-session-open window, the
            video's claimed highest-volatility stretch for BTC, 6yr lookback)
    Range  ATM straddle price = market-implied 1-day range. No historical BTC
            option chain exists in this project (crypto/ only trades perps —
            see crypto/delta/products.py), so the straddle is proxied the same
            way index_ai/strategies/options_cpr/premium.py prices index
            options: zero-rate Black-Scholes over a REALIZED-volatility input
            (trailing rolling stdev of real candle returns), not a guessed
            constant. This is a stronger proxy than options_cpr's static IV
            knob, precisely because real underlying history exists here.
    Strikes 1.8x the 1-day proxy range, each side, using the expiry TWO days
            out (the video skips the next expiry deliberately -- entering
            right as volatility dies means the near expiry's premium already
            decayed, so it shifts to the one after for a fatter premium).
    SL     each leg closed independently at 2x its own entry premium (not a
            combined-position stop).
    Exit   ~17:28 IST two days later (just before the window repeats), or
            expiry if nothing stopped first.

    python -m scripts.backtest_btc_strangle                     # BTCUSD, full cached history
    python -m scripts.backtest_btc_strangle --instruments BTCUSD ETHUSD
    python -m scripts.backtest_btc_strangle --range-mult 1.5 --strike-step 250
    python -m scripts.backtest_btc_strangle --margin-usd 200 --leverage 10
    python -m scripts.backtest_btc_strangle --fetch              # also top up the cache from Delta

Lots are sized per trade from a $ margin budget at that trade's entry spot
(crypto.sizing.lots_from_margin_budget -- the same formula and the same
--margin-usd/--leverage default knobs, CRYPTO_MARGIN_PER_POSITION_USD and
CRYPTO_LEVERAGE, every perp lane in this project already uses), not a fixed
lot count. The budget and leverage stay constant across the whole backtest;
the lot count a given trade affords varies with spot at that trade's entry.

A DIFFERENT, already-implemented BTC options lane exists in this codebase:
crypto/btc_straddle.py (paper-only, CRYPTO_BTC_STRADDLE_ENABLED, studied from
a separate "Theta Gainers" video) sells the ATM straddle and holds to the
NEXT day's expiry -- no OTM offset, no skip-an-expiry, combined-credit TP/SL
rather than this spec's per-leg independent SL. Different strategy, same
venue. Its code is the one place in this project that has actually priced a
crypto option against Delta's real product list (crypto/delta/options.py,
added after this script was first written) and had its options fee
assumption trading-safety-reviewed, so this script's cost model was aligned
to it rather than inventing a separate convention -- see fee model below.

UNVERIFIED ASSUMPTIONS -- this session has no network path to Delta Exchange
(egress-blocked) and no cached options data, so none of the below could be
checked against the live venue. Confirm each on a machine with real API
access before trusting an absolute dollar figure, and override via CLI:
  * --contract-value   default 0.001 BTC/lot. Matches both the PERP contract
    spec (crypto/sizing.py) AND the live options product's own default
    (crypto/delta/options.py: `contract_value=float(p.get(...) or 0.001)`) --
    more confidence than a pure guess, but still not fetched live here.
  * fee model          reuses crypto.charges.fee_usd(notional, taker=...) on
    the underlying notional (size * contract_value * spot), taker by default
    -- the exact convention crypto/btc_straddle.py uses after its
    trading-safety review, in place of a separate guessed options-fee rate.
    Its own docstring still flags this as unconfirmed against a real fill
    (Delta's options fee may be capped as a fraction of premium instead);
    that caveat carries over here unchanged. --maker switches to the maker
    rate; there is no separate --taker-fee-pct any more.
  * --margin-usd/--leverage  lots are sized with crypto.sizing's PERP margin
    formula (contract_value * spot / leverage), reused here for options at
    the user's request -- a real short option's exchange margin is not
    simply notional/leverage (it typically reflects the option's own max-loss
    or a SPAN-style calc), so the lot count this produces is a convenience
    consistent with the rest of this codebase, not a verified options margin
    requirement. Confirm against Delta's real margin API before sizing a
    live position this way.
  * --strike-step       default $500 -- a guess at Delta's BTC option strike
    granularity, not fetched from a live option chain (none exists to fetch
    here; crypto/delta/options.py *could* list the real strikes on a machine
    with network access -- worth switching to for a tighter backtest).
  * --half-spread-bps   default 150bps of premium (video showed ~$3-4 wide
    on a ~$150-900 premium at book Level 1) -- a flat assumption. Even the
    live btc_straddle.py paper lane models zero slippage (reads mark_price
    directly), so this script is already more conservative than that lane,
    but still unmeasured -- no options order-book history exists to measure
    from, unlike crypto/charges.py's measured perp half-spread.
  * The video's liquidity/Level-1-book-depth argument for low slippage is
    BTC-specific. Running --instruments on ETHUSD etc. reuses the same flat
    cost assumptions with no basis for assuming they hold there too.

Writes research/btc_strangle/.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

from crypto.candle_cache import cached_candles
from crypto.charges import fee_usd
from crypto.config import crypto_settings
from crypto.delta.products import Contract
from crypto.sizing import lots_from_margin_budget
from index_ai.market_clock import IST
from index_ai.strategies.options_cpr.premium import bs_price_delta

OUT = Path("research/btc_strangle")

_MINUTES_PER_YEAR_247 = 365.0 * 24.0 * 60.0  # crypto trades every minute, every day
_ENTRY_EXCLUSION_START = dtime(18, 0)
_ENTRY_EXCLUSION_END = dtime(22, 30)
_EXPIRY_TIME = dtime(17, 30)  # Delta's daily BTC option expiry clock time (IST)


@dataclass
class Params:
    range_mult: float = 1.8
    strike_step: float = 500.0
    entry_time: dtime = dtime(22, 45)
    exit_lead_minutes: int = 5  # exit this many minutes before the next expiry's clock time
    expiry_offset_days: int = 2  # skip the next expiry, use the one after
    sl_mult: float = 2.0  # per-leg SL = this x entry premium
    realized_vol_days: int = 20  # trailing lookback for the IV proxy
    contract_value: float = 0.001  # BTC per lot -- see UNVERIFIED ASSUMPTIONS
    # Lots are no longer a fixed count: sized per trade from a $ margin budget
    # at that trade's entry spot, same formula + knobs crypto.sizing uses for
    # every perp lane (crypto.config.crypto_settings().margin_per_position_usd
    # / .leverage are this script's defaults -- see main()). A bigger BTC move
    # before entry means fewer, bigger-notional lots fit the same budget, and
    # vice versa; the budget and leverage stay constant, the lot count doesn't.
    margin_usd: float = 50.0
    leverage: float = 20.0
    taker: bool = True  # crypto.charges.fee_usd rate to apply -- see UNVERIFIED ASSUMPTIONS
    half_spread_bps: float = 150.0  # bps of premium, both legs, both sides


def _annualized_vol(returns: pd.Series) -> float:
    """Trailing stdev of log returns, annualized for a 24/7, 1h-bar calendar."""
    if len(returns) < 8:
        return 0.0
    return float(returns.std(ddof=1)) * math.sqrt(24.0 * 365.0)


def _leg_premium(spot: float, strike: float, is_call: bool, iv: float, minutes_to_expiry: float) -> float:
    t_years = max(0.0, minutes_to_expiry) / _MINUTES_PER_YEAR_247
    return bs_price_delta(spot, strike, t_years, iv, is_call)[0]


def _round_step(value: float, step: float) -> float:
    return round(value / step) * step


def _next_expiry(after: datetime, offset_days: int) -> datetime:
    """The expiry_offset_days-th daily expiry strictly after ``after`` (IST)."""
    ist_after = after.astimezone(IST)
    day = ist_after.date()
    first_expiry = datetime.combine(day, _EXPIRY_TIME, tzinfo=IST)
    if first_expiry <= ist_after:
        day = day + timedelta(days=1)
    return datetime.combine(day + timedelta(days=offset_days - 1), _EXPIRY_TIME, tzinfo=IST)


def _slippage_usd(premium_per_unit: float, coins: float, bps: float) -> float:
    return abs(premium_per_unit) * coins * bps / 10_000.0


@dataclass
class Trade:
    symbol: str
    entry_ts: str
    exit_ts: str
    lots: int
    spot_entry: float
    call_strike: float
    put_strike: float
    iv_proxy: float
    call_premium_entry: float
    put_premium_entry: float
    call_closed_via_sl: bool
    put_closed_via_sl: bool
    gross_usd: float
    fees_usd: float
    slippage_usd: float
    net_usd: float
    bars_held: int


def _run_symbol(symbol: str, candles: pd.DataFrame, p: Params) -> list[Trade]:
    """One short strangle per calendar day, entered once outside the US-session window."""
    if candles.empty:
        return []
    # product_id/tick_size/min_size are unused by lots_from_margin_budget's
    # math (margin = contract_value * mark / leverage) -- placeholders here,
    # real options contract would come from crypto/delta/options.py live.
    contract = Contract(
        symbol=symbol, product_id=0, contract_value=p.contract_value,
        tick_size=0.0, min_size=1.0, max_leverage=p.leverage,
    )
    candles = candles.sort_values("datetime").reset_index(drop=True)
    candles["ist"] = candles["datetime"].dt.tz_convert(IST)
    log_ret = (candles["close"] / candles["close"].shift(1)).apply(
        lambda r: math.log(r) if r and r > 0 else 0.0
    )

    trades: list[Trade] = []
    start_day = candles["ist"].iloc[0].date() + timedelta(days=p.realized_vol_days + 1)
    end_day = candles["ist"].iloc[-1].date()
    day = start_day
    while day <= end_day:
        entry_wall = datetime.combine(day, p.entry_time, tzinfo=IST)
        # the exclusion window check is the whole point of the strategy
        assert not (_ENTRY_EXCLUSION_START <= p.entry_time <= _ENTRY_EXCLUSION_END)

        idx = candles.index[candles["ist"] >= entry_wall]
        if len(idx) == 0:
            day += timedelta(days=1)
            continue
        entry_i = int(idx[0])
        entry_wall = candles["ist"].iloc[entry_i]  # the actual bar used, not the wall-clock target
        lookback_start = entry_wall - timedelta(days=p.realized_vol_days)
        window = log_ret.iloc[:entry_i][candles["ist"].iloc[:entry_i] >= lookback_start]
        sigma = _annualized_vol(window)
        if sigma <= 0:
            day += timedelta(days=1)
            continue
        # skip a day whose hold would run past the end of the cached history --
        # a truncated run produces a fake "never stopped" trade, not a real one
        if candles["ist"].iloc[-1] < _next_expiry(entry_wall, p.expiry_offset_days) - timedelta(
            minutes=p.exit_lead_minutes
        ):
            day += timedelta(days=1)
            continue

        spot = float(candles["close"].iloc[entry_i])
        one_day_range = spot * sigma * math.sqrt(1.0 / 365.0)
        strike_dist = one_day_range * p.range_mult
        call_strike = _round_step(spot + strike_dist, p.strike_step)
        put_strike = _round_step(spot - strike_dist, p.strike_step)

        expiry = _next_expiry(entry_wall, p.expiry_offset_days)
        exit_wall = expiry - timedelta(minutes=p.exit_lead_minutes)
        minutes_to_expiry = (expiry - entry_wall).total_seconds() / 60.0

        call_entry_px = _leg_premium(spot, call_strike, True, sigma, minutes_to_expiry)
        put_entry_px = _leg_premium(spot, put_strike, False, sigma, minutes_to_expiry)
        # sized from the $ budget at THIS trade's spot, not a fixed lot count --
        # a $84k BTC and a $60k BTC afford a different number of 0.001-BTC
        # contracts for the same margin_usd/leverage
        lots = lots_from_margin_budget(
            contract, spot, margin_usd=p.margin_usd, leverage=p.leverage
        )
        coins = lots * p.contract_value
        underlying_notional = coins * spot

        open_fee = fee_usd(underlying_notional, taker=p.taker) * 2.0
        open_slip = _slippage_usd(call_entry_px, coins, p.half_spread_bps) + _slippage_usd(
            put_entry_px, coins, p.half_spread_bps
        )

        call_open, put_open = True, True
        call_sl, put_sl = False, False
        call_close_px = put_close_px = 0.0
        bars_held = 0
        exit_i = entry_i
        for j in range(entry_i + 1, len(candles)):
            ts = candles["ist"].iloc[j]
            if ts > exit_wall:
                break  # keep exit_i at the last bar at/before exit_wall, not this one
            exit_i = j
            bars_held += 1
            if not (call_open or put_open):
                break
            t_remaining = max(0.0, (expiry - ts.to_pydatetime()).total_seconds() / 60.0)
            hi, lo = float(candles["high"].iloc[j]), float(candles["low"].iloc[j])
            if call_open:
                px_now = _leg_premium(hi, call_strike, True, sigma, t_remaining)
                if px_now >= p.sl_mult * call_entry_px:
                    call_open, call_sl, call_close_px = False, True, px_now
            if put_open:
                px_now = _leg_premium(lo, put_strike, False, sigma, t_remaining)
                if px_now >= p.sl_mult * put_entry_px:
                    put_open, put_sl, put_close_px = False, True, px_now

        spot_exit = float(candles["close"].iloc[exit_i])
        t_exit = max(0.0, (expiry - candles["ist"].iloc[exit_i].to_pydatetime()).total_seconds() / 60.0)
        if call_open:
            call_close_px = _leg_premium(spot_exit, call_strike, True, sigma, t_exit)
        if put_open:
            put_close_px = _leg_premium(spot_exit, put_strike, False, sigma, t_exit)

        close_fee = fee_usd(underlying_notional, taker=p.taker) * 2.0
        close_slip = _slippage_usd(call_close_px, coins, p.half_spread_bps) + _slippage_usd(
            put_close_px, coins, p.half_spread_bps
        )

        gross = (call_entry_px + put_entry_px - call_close_px - put_close_px) * coins
        fees = open_fee + close_fee
        slip = open_slip + close_slip
        net = gross - fees - slip

        trades.append(
            Trade(
                symbol=symbol,
                entry_ts=entry_wall.isoformat(),
                exit_ts=candles["ist"].iloc[exit_i].isoformat(),
                lots=lots,
                spot_entry=round(spot, 2),
                call_strike=call_strike,
                put_strike=put_strike,
                iv_proxy=round(sigma, 4),
                call_premium_entry=round(call_entry_px, 2),
                put_premium_entry=round(put_entry_px, 2),
                call_closed_via_sl=call_sl,
                put_closed_via_sl=put_sl,
                gross_usd=round(gross, 2),
                fees_usd=round(fees, 2),
                slippage_usd=round(slip, 2),
                net_usd=round(net, 2),
                bars_held=bars_held,
            )
        )
        day += timedelta(days=1)
    return trades


def _agg(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    nets = [t.net_usd for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    eq = peak = maxdd = 0.0
    for n in nets:
        eq += n
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    both_win = sum(1 for t in trades if not t.call_closed_via_sl and not t.put_closed_via_sl and t.net_usd > 0)
    one_sl = sum(1 for t in trades if t.call_closed_via_sl != t.put_closed_via_sl)
    both_sl = sum(1 for t in trades if t.call_closed_via_sl and t.put_closed_via_sl)
    return {
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(nets), 1),
        "net_usd": round(sum(nets), 2),
        "gross_usd": round(sum(t.gross_usd for t in trades), 2),
        "fees_usd": round(sum(t.fees_usd for t in trades), 2),
        "slippage_usd": round(sum(t.slippage_usd for t in trades), 2),
        "avg_win": round(st.mean(wins), 2) if wins else 0,
        "avg_loss": round(st.mean(losses), 2) if losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_drawdown_usd": round(maxdd, 2),
        "both_legs_profit_pct": round(100 * both_win / len(trades), 1),
        "one_leg_sl_pct": round(100 * one_sl / len(trades), 1),
        "both_legs_sl_pct": round(100 * both_sl / len(trades), 1),
        "span": f"{trades[0].entry_ts[:10]} .. {trades[-1].entry_ts[:10]}",
    }


def main() -> None:
    s = crypto_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="*", default=["BTCUSD"],
                     help="video's liquidity argument is BTC-specific; pass more at your own risk")
    ap.add_argument("--days", type=float, default=0.0, help="0 = all cached history")
    ap.add_argument("--range-mult", type=float, default=Params.range_mult)
    ap.add_argument("--strike-step", type=float, default=Params.strike_step)
    ap.add_argument("--sl-mult", type=float, default=Params.sl_mult)
    ap.add_argument("--expiry-offset-days", type=int, default=Params.expiry_offset_days)
    ap.add_argument("--realized-vol-days", type=int, default=Params.realized_vol_days)
    ap.add_argument("--contract-value", type=float, default=Params.contract_value)
    ap.add_argument("--margin-usd", type=float, default=s.margin_per_position_usd,
                     help="$ margin budget per trade -- lots are sized from this at each "
                          "entry's spot, not fixed (default: crypto.config's "
                          "CRYPTO_MARGIN_PER_POSITION_USD, same knob every perp lane uses)")
    ap.add_argument("--leverage", type=float, default=s.leverage,
                     help="default: crypto.config's CRYPTO_LEVERAGE, same knob every perp lane uses")
    ap.add_argument("--maker", action="store_true", help="use crypto.charges maker rate instead of taker")
    ap.add_argument("--half-spread-bps", type=float, default=Params.half_spread_bps)
    ap.add_argument("--fetch", action="store_true", help="also top up the local cache from Delta's live API")
    args = ap.parse_args()

    p = Params(
        range_mult=args.range_mult, strike_step=args.strike_step, sl_mult=args.sl_mult,
        expiry_offset_days=args.expiry_offset_days, realized_vol_days=args.realized_vol_days,
        contract_value=args.contract_value, margin_usd=args.margin_usd, leverage=args.leverage,
        taker=not args.maker, half_spread_bps=args.half_spread_bps,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    configured = set(s.symbols)
    summary: dict[str, dict] = {}
    all_trades: dict[str, list[Trade]] = {}

    for symbol in [s.upper() for s in args.instruments]:
        if symbol not in configured:
            print(f"{symbol}: not in CRYPTO_SYMBOLS ({sorted(configured)}) -- skip")
            continue
        days = args.days or 3650.0
        frame = cached_candles(symbol, "1h", days=days, refresh=args.fetch)
        if frame.empty:
            print(f"{symbol}: no cached 1h candles (run with --fetch on a machine with Delta access "
                  f"to populate crypto/candle_cache first) -- skip")
            continue
        print(f"{symbol}: replaying {len(frame)} 1h bars...", flush=True)
        trades = _run_symbol(symbol, frame, p)
        all_trades[symbol] = trades
        a = _agg(trades)
        summary[symbol] = a
        if trades:
            (OUT / f"{symbol}_trades.json").write_text(
                json.dumps([t.__dict__ for t in trades], indent=2), encoding="utf-8"
            )
            print(f"  {symbol} ({a['span']}): {a['trades']} trades, {a['win_rate_pct']}% win, "
                  f"PF {a['profit_factor']}, net ${a['net_usd']:,} (gross ${a['gross_usd']:,}, "
                  f"fees ${a['fees_usd']:,}, slip ${a['slippage_usd']:,}), maxDD ${a['max_drawdown_usd']:,}",
                  flush=True)
            print(f"    both-legs-profit {a['both_legs_profit_pct']}% / one-leg-SL {a['one_leg_sl_pct']}% / "
                  f"both-legs-SL {a['both_legs_sl_pct']}%", flush=True)
        else:
            print(f"  {symbol}: no trades produced (insufficient history for the "
                  f"{p.realized_vol_days}-day vol lookback?)")

    lines = [
        "# BTC short strangle -- US-session-skip spec, Black-Scholes-over-realized-vol proxy\n",
        "_No historical BTC option chain exists in this project; premiums are a zero-rate "
        "Black-Scholes proxy driven by REALIZED volatility (not a live option-market straddle "
        "price). Contract value, strike step, taker fee %, and half-spread are UNVERIFIED "
        "defaults (see the module docstring) -- this session has no network path to Delta "
        "Exchange to confirm them. Relative comparisons (win rate, which filters help) are "
        "meaningful; absolute dollars are not, until those are confirmed and real data is "
        "substituted.\n",
        "| Symbol | Span | Trades | Win% | PF | Net $ | Gross $ | Fees $ | Slip $ | Max DD $ | "
        "Both-win% | One-SL% | Both-SL% |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for sym, a in summary.items():
        if a.get("trades"):
            lines.append(
                f"| {sym} | {a['span']} | {a['trades']} | {a['win_rate_pct']} | {a['profit_factor']} | "
                f"{a['net_usd']:,} | {a['gross_usd']:,} | {a['fees_usd']:,} | {a['slippage_usd']:,} | "
                f"{a['max_drawdown_usd']:,} | {a['both_legs_profit_pct']} | {a['one_leg_sl_pct']} | "
                f"{a['both_legs_sl_pct']} |"
            )
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nWrote research/btc_strangle/report.md")


if __name__ == "__main__":
    main()
