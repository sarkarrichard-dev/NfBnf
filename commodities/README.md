# commodities/ — MCX commodity futures

A third section alongside the index (`index_ai/`) and crypto (`crypto/`) lanes.
Same broker (Dhan), so it reuses the whole broker layer; different session and
instruments, so it runs on its own scan task.

## What it trades

MCX **mini / micro** futures — small notional to keep margin per lot low:

| key | contract | ₹ P&L per 1.0 move / lot |
|---|---|---|
| `CRUDEOILM` | Crude Oil Mini (10 bbl) | 10 |
| `NATGASMINI` | Natural Gas Mini (250 mmBtu) | 250 |
| `GOLDM` | Gold Mini (100 g) | 10 |
| `SILVERMIC` | Silver Micro (1 kg) | 1 |

The front-month security id / lot / expiry rolls monthly and is resolved from the
Dhan scrip master by `python -m scripts.fetch_commodity_universe` →
`memory/commodity_universe.json`. Re-run monthly.

## Session

MCX runs **09:00–23:30 IST** (23:55 for these US-linked contracts while US
daylight time is on). New entries stop 25 min before the close; open positions
are squared off 5 min before it. Weekdays only (`MCX_EXTRA_HOLIDAYS` in `.env`
extends the skip list). Runs entirely on the evening the equity scanner is shut.

## Strategy

**Reuses the index directional signal** — `index_ai.strategies.futures.engine`
(CPR bias + EMA 9/21 + Supertrend 10,3 on 15m; 5m EMA reclaim entry). Not the
crypto strategies. Risk is percent-of-price (a crude point and a gold point are
nothing alike) — `initial_stop_pct` / `trail_*_pct` on `CommoditySpec`. `FUT_*`
env overrides are shared with the index futures lane.

## Charges

`commodities/charges.py`, from the Dhan schedule: ₹20/order + MCX txn 0.0021% +
CTT 0.01% (sell) + SEBI 0.0001% + stamp 0.002% (buy) + 18% GST. A CRUDEOILM
round trip is ~₹78 on ~₹60k notional — a tiny fraction of a normal daily move,
which is the point of trading outright futures instead of option spreads.

## Paper only

`ENABLE_COMMODITIES_PAPER` (default on). The `_commodities_paper_loop` task in
`server.py` calls `scan_commodities_paper()` ~every 60 s. **Never wired to live
orders** — the reused signal backtests negative on index/stock futures; this is a
forward-record lane. State: `memory/commodity_state.json`; closed trades:
`memory/commodity_journal.jsonl`.
