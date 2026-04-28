# QuantTape Bot Roadmap

This project is now explicitly a research console. It is not a live autonomous trading bot yet.

## What It Can Do Now

- Scan local tabular files into a SQLite profile catalog.
- Show how many files, bytes, sampled rows, and ingest errors exist.
- Pull historical OHLCV from Yahoo Finance.
- Compute simple structural signals from price and volume.
- Ask an LLM for a narrative when remote credentials are configured.
- Store your feedback as per-signal EMA memory.
- Build a supervised feature/label table from OHLCV.
- Run a research-only walk-forward backtest.

## What It Cannot Do Yet

- It does not train a production-grade predictive model.
- It does not place broker orders.
- It does not know option fills, slippage, taxes, liquidity, or broker margin.
- It does not satisfy live retail algo compliance by itself.
- It does not prove profitability.

## Gates Before Live Automation

1. Confirm data integrity and symbol mapping.
2. Define the exact tradable strategy and labels.
3. Backtest with realistic costs and no look-ahead bias.
4. Paper trade for at least 20 market sessions.
5. Add broker API integration only after paper trading works.
6. Add max daily loss, position size, trade count, stale-data detection, and a kill switch.
7. Keep every order traceable with reason, signal, data timestamp, and model version.

## Recommended Next Build

The option-data parser and local option heatmap foundation now exist. The next serious milestone is
normalizing all option CSVs into one queryable research store, then adding Dhan security-ID mappings
so live quote/full packets can update the same heatmap surface.

## Dhan Data Feed Plan

1. Add `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` to `.env`.
2. Build a Dhan instrument master mapper: symbol/expiry/strike/CE/PE to security ID.
3. Subscribe to quote/full packets for the selected chain.
4. Decode binary feed packets into a live in-memory quote cache.
5. Feed the existing Options Heatmap UI from Dhan when market is live, and local CSV when reviewing history.
6. Keep live order placement disabled until the readiness checklist is complete.
