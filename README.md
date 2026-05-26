# Index Options AI

Fresh CPR + EMA intraday workstation for only:

- NIFTY
- BANKNIFTY

It can read Dhan market data, choose an option strike from the option chain, create one-lot paper/live trade plans, download Dhan history, backtest from CSV candles, and learn from feedback/trade outcomes.

Live orders are blocked by default. Put Dhan app credentials and the daily access token in `.env`, then change `TRADING_MODE=LIVE` and `ALLOW_LIVE_TRADING=true` only when you are ready.

## Start

Double-click:

```text
Start Index Options AI.cmd
```

The launcher is a controller with Start, Stop, Restart, Status, and Open Dashboard options. Use Stop before closing when you want port 8000 released.

Or run manually:

```powershell
python -m index_ai.server
```

Open:

```text
http://127.0.0.1:8000
```

## Dhan Login

The app stores Dhan API key/secret separately from the daily access token. Dhan's API key/secret flow still requires your Dhan Client ID and a browser login to produce a 24-hour access token. Use the dashboard Dhan Login panel after adding `DHAN_CLIENT_ID`.

## Where Things Are

- `index_ai` - strategy brain, Dhan client, learning loop, trade execution gate.
- `dashboard` - clean browser screen.
- `memory` - local trade journal, feedback, model settings, datasets, and backtest CSVs.
- `guides` - plain-language notes.

## Data For Backtesting

Use the dashboard Data Library to download:

- Daily history: up to seven years for broader CPR/backtest context.
- Intraday history: up to five years in 90-day chunks, matching Dhan's documented intraday limit.

The files are saved in `memory/datasets/`. You can also put your own candle CSV files in `memory/backtests/`.

Required columns:

```text
datetime,open,high,low,close
```

Then run a backtest from the dashboard.

## Learning Loop

Every planned or executed paper/live trade is journaled locally. Feedback and trade outcomes update a learned confidence adjustment, so weak recent results make the system more selective and strong recent results let it become slightly more permissive.
