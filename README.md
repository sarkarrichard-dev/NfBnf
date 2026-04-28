# NfBnf / QuantTape

Indian-market research console for local option-chain data, Yahoo OHLCV analysis, Dhan live-data readiness, and research-only backtesting.

## Current Mode

This repository is intentionally **research-only**. Live order placement is disabled until the risk, compliance, paper-trading, and broker-integration gates are complete.

## What It Does

- Profiles local tabular market data into a SQLite catalog.
- Shows an audit of local files, source size, sampled rows, and ingest errors.
- Reads local NIFTY option CSV files into an option-chain heatmap.
- Pulls Yahoo OHLCV for equity/index research.
- Builds explicit ML-ready feature/label tables from historical candles.
- Runs walk-forward research backtests.
- Prepares DhanHQ live-data configuration for future live heatmaps.

## What It Does Not Do Yet

- It does not place live trades.
- It does not train a production trading model.
- It does not guarantee profitable signals.
- It does not upload or commit your local market data.

## Setup

```powershell
python -m pip install -e .
```

Optional Excel/Parquet/Hugging Face helpers:

```powershell
python -m pip install -e ".[data,hf]"
```

Copy `.env.example` to `.env` and fill only the credentials you actually use.

## Run The App

```powershell
python -m nbnf.server.main
```

Then open:

```text
http://127.0.0.1:8000
```

Windows helper scripts live in `scripts/windows/`.

## Local Data

Local data folders are ignored by Git:

- `data/`
- `data for ml/`
- `local_data/`

Keep your large historical CSV files there locally. They are used for research and heatmaps, but they should not be pushed to GitHub.

## Dhan Live Data

Add these to `.env` when ready:

```env
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=
DHAN_API_BASE_URL=https://api.dhan.co/v2
DHAN_FEED_URL=wss://api-feed.dhan.co
NBNF_ENABLE_LIVE_TRADING=false
```

Dhan is currently used as a planned live market-data provider. Live order placement remains blocked.

## Roadmap

See [docs/BOT_ROADMAP.md](docs/BOT_ROADMAP.md).
