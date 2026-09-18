"""Backtest the two new TA-Lib/backtrader strategies (scripts/talib_strategies.py)
on real historical data, both markets Richard asked for (2026-09-18):

  * India: NIFTY / BANKNIFTY / SENSEX index-futures, spot-replay (same
    approximation ``index_ai.strategies.futures.backtest`` already uses --
    futures track spot closely, no premium/theta guesswork). Real charges
    via ``index_ai.charges.futures_round_trip_rupees`` /
    ``futures_slippage_rupees``, same schedule every other futures backtest
    on this platform uses.
  * Crypto: the 6 live-configured Delta perps. Real charges via
    ``crypto.charges.round_trip_cost_usd`` (the real Delta taker fee + GST +
    measured/fallback half-spread), same sizing convention
    (``crypto.sizing``) the live paper lanes use.

No new cost model invented -- both venues route through the exact functions
already trusted elsewhere on this platform, so results are directly
comparable to the existing RESULTS.md / STOCK_RESULTS.md tables.

    python -m scripts.backtest_talib_backtrader --market india --days 730
    python -m scripts.backtest_talib_backtrader --market crypto --days 120
    python -m scripts.backtest_talib_backtrader --market both
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import backtrader as bt
import pandas as pd

from scripts.talib_strategies import TALibCandleStrategy, TALibMacdStochStrategy

STRATEGIES = {
    "talib_candle": TALibCandleStrategy,
    "talib_macd_stoch": TALibMacdStochStrategy,
}

INDIA_INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"]
CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "PAXGUSD", "XRPUSD", "BNBUSD"]


@dataclass
class Trade:
    strategy: str
    venue: str
    instrument: str
    side: str
    entry_price: float
    exit_price: float
    entry_dt: str
    exit_dt: str
    entry_reason: str
    exit_reason: str
    net: float  # rupees (india) or usd (crypto), after real costs


def _run_backtrader(df: pd.DataFrame, strat_cls: type[bt.Strategy]) -> list[dict[str, Any]]:
    """Run one strategy over one instrument's OHLCV frame, return its raw
    (pre-cost) trade dicts."""
    cerebro = bt.Cerebro(stdstats=False)
    feed = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(feed)
    cerebro.addstrategy(strat_cls)
    results = cerebro.run()
    return results[0].trades


# --------------------------------------------------------------------------
# India: spot-replay on 15m bars, real futures charges
# --------------------------------------------------------------------------


def _india_15m_frame(instrument: str, days: int) -> pd.DataFrame:
    from index_ai.candle_cache import load_cached_range

    to_day = date.today()
    from_day = to_day - timedelta(days=days)
    raw = load_cached_range(instrument, "1", from_date=from_day, to_date=to_day)
    if raw.empty:
        return raw
    raw = raw.set_index(pd.to_datetime(raw["datetime"]))
    ohlc = raw[["open", "high", "low", "close", "volume"]].resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return ohlc.dropna()


def backtest_india(strategy_name: str, days: int) -> list[Trade]:
    from index_ai.charges import futures_round_trip_rupees, futures_slippage_rupees
    from index_ai.strategies.futures.config import config_for

    strat_cls = STRATEGIES[strategy_name]
    out: list[Trade] = []
    for inst in INDIA_INSTRUMENTS:
        df = _india_15m_frame(inst, days)
        if len(df) < 200:
            print(f"  {inst}: not enough cached 15m data ({len(df)} bars), skipping")
            continue
        lot = config_for(inst).lot_size
        for raw in _run_backtrader(df, strat_cls):
            direction = 1 if raw["side"] == "long" else -1
            gross_pts = (raw["exit_price"] - raw["entry_price"]) * direction
            gross_rupees = gross_pts * lot
            cost = futures_round_trip_rupees(raw["entry_price"], lot, inst) + futures_slippage_rupees(
                lot, inst
            )
            out.append(
                Trade(
                    strategy=strategy_name,
                    venue="india",
                    instrument=inst,
                    side=raw["side"],
                    entry_price=raw["entry_price"],
                    exit_price=raw["exit_price"],
                    entry_dt=str(raw["entry_dt"]),
                    exit_dt=str(raw["exit_dt"]),
                    entry_reason=raw["entry_reason"],
                    exit_reason=raw["exit_reason"],
                    net=round(gross_rupees - cost, 2),
                )
            )
    return out


# --------------------------------------------------------------------------
# Crypto: 1h bars, real Delta charges + sizing
# --------------------------------------------------------------------------


def _crypto_1h_frame(symbol: str, days: float) -> pd.DataFrame:
    from crypto.candle_cache import cached_candles

    raw = cached_candles(symbol, "1h", days=days)
    if raw.empty:
        return raw
    df = raw.set_index(pd.to_datetime(raw["datetime"]))
    return df[["open", "high", "low", "close", "volume"]]


def backtest_crypto(strategy_name: str, days: float) -> list[Trade]:
    from crypto.backtest import _contract
    from crypto.charges import round_trip_cost_usd
    from crypto.config import crypto_settings
    from crypto.sizing import lots_from_margin_budget, size_position

    strat_cls = STRATEGIES[strategy_name]
    s = crypto_settings()
    out: list[Trade] = []
    for sym in CRYPTO_SYMBOLS:
        df = _crypto_1h_frame(sym, days)
        if len(df) < 200:
            print(f"  {sym}: not enough candle history ({len(df)} bars), skipping")
            continue
        contract = _contract(sym)
        for raw in _run_backtrader(df, strat_cls):
            entry_px = float(raw["entry_price"])
            exit_px = float(raw["exit_price"])
            direction = 1 if raw["side"] == "long" else -1
            lots = lots_from_margin_budget(
                contract, entry_px, margin_usd=s.margin_per_position_usd, leverage=s.leverage
            )
            sr = size_position(
                contract, entry_px, lots=lots, deploy_usd=s.deploy_usd, leverage=s.leverage,
                wallet_usd=s.paper_bankroll_usd,
            )
            size = sr.size if sr.ok else 1
            coins = size * contract.contract_value
            notional = coins * entry_px
            gross = (exit_px - entry_px) * direction * coins
            cost = round_trip_cost_usd(notional, sym, exit_px, size, contract.contract_value)
            out.append(
                Trade(
                    strategy=strategy_name,
                    venue="crypto",
                    instrument=sym,
                    side=raw["side"],
                    entry_price=entry_px,
                    exit_price=exit_px,
                    entry_dt=str(raw["entry_dt"]),
                    exit_dt=str(raw["exit_dt"]),
                    entry_reason=raw["entry_reason"],
                    exit_reason=raw["exit_reason"],
                    net=round(gross - cost, 2),
                )
            )
    return out


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _report(trades: list[Trade], venue: str, currency: str) -> None:
    rows = [t for t in trades if t.venue == venue]
    if not rows:
        print(f"  ({venue}: no trades)")
        return
    by_inst: dict[str, list[Trade]] = {}
    for t in rows:
        by_inst.setdefault(t.instrument, []).append(t)

    total_n = len(rows)
    total_net = sum(t.net for t in rows)
    wins = sum(1 for t in rows if t.net > 0)
    print(
        f"  {venue}: {total_n} trades, net {currency}{total_net:,.2f}, "
        f"win rate {wins / total_n:.0%}"
    )
    for inst, ts in sorted(by_inst.items()):
        n = len(ts)
        net = sum(t.net for t in ts)
        w = sum(1 for t in ts if t.net > 0)
        print(f"    {inst:<10} {n:>4} trades  net {currency}{net:>12,.2f}  win {w / n:.0%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", choices=["india", "crypto", "both"], default="both")
    ap.add_argument("--days", type=int, default=None, help="lookback (default: 730 india / 120 crypto)")
    ap.add_argument("--strategy", choices=list(STRATEGIES), action="append")
    args = ap.parse_args()

    strategies = args.strategy or list(STRATEGIES)
    markets = ["india", "crypto"] if args.market == "both" else [args.market]

    for strat_name in strategies:
        print(f"\n=== {strat_name} ===")
        all_trades: list[Trade] = []
        if "india" in markets:
            days = args.days or 730
            print(f"India ({days}d, 15m spot-replay):")
            all_trades += backtest_india(strat_name, days)
            _report(all_trades, "india", "Rs ")
        if "crypto" in markets:
            days = args.days or 120
            print(f"Crypto ({days}d, 1h):")
            crypto_trades = backtest_crypto(strat_name, float(days))
            all_trades += crypto_trades
            _report(all_trades, "crypto", "$")


if __name__ == "__main__":
    main()
