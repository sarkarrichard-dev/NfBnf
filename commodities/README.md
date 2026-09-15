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

## Strategy candidates tested and rejected

**Liquidity sweep + Open Interest confirmation + volume-profile gate**
(2026-09-15, `scripts/backtest_liquidity_sweep_oi.py`) — Richard asked whether
the platform understood liquidity sweeps, order flow and volume profile, then
asked for a strategy combining them for futures/commodities, a section that
had only ever reused the index signal. A plain version of this idea ("mark
yesterday's high/low, fade the sweep, target the opposite extreme") was
already tested on this section and on the Indian index/stock futures back in
2026-09-11 and came back net-negative on every setting — the code was
deleted and this is **not to be rebuilt** as a plain price-action strategy.
This version tests something genuinely different: it only fades the sweep
when real Open Interest (fetched from Dhan with `oi=true`, confirmed to
return real, continuously varying history for these contracts) shows the
move was unwinding rather than fresh conviction, and only when the sweep
price sits outside the recent volume profile's value area (a thinly-traded
level, not one with real acceptance).

Backtest, 500 days, real MCX charges, all four commodities:

| symbol | trades | net rupees | win rate |
|---|---:|---:|---:|
| CRUDEOILM | 13 | **−₹2,062** | 38% |
| NATGASMINI | 12 | **−₹3,576** | 17% |
| GOLDM | 5 | **−₹15,456** | 20% |
| SILVERMIC | 18 | **+₹3,832** | 56% |
| **total** | **48** | **−₹17,261** | 38% |

Net-negative overall — 3 of 4 commodities lose, and the one winner
(SILVERMIC) is a small sample (18 trades). Adding real order-flow and
volume-profile confirmation on top of the sweep did not rescue the family of
idea; the honest prior stated before this was built ("probably no edge,
since the plain version already failed everywhere") held. **Not wired into
any lane.** Kept as research tooling (`scripts/backtest_liquidity_sweep_oi.py`)
and documented here so this specific combination — sweep + OI + volume
profile, not just the plain sweep — isn't re-tested from scratch later.

Scope note: this was tested on commodities only, not the Indian index or
stock futures paper lanes. Those two trade the cash/spot price as a proxy for
the future (see `index_ai/strategies/futures/paper.py`), not the actual
futures contract, so there is no real Open Interest to read for what they
currently fetch — extending this idea there would first need each
instrument's actual futures-contract security id resolved, which isn't done
today.

## Paper only

`ENABLE_COMMODITIES_PAPER` (default on). The `_commodities_paper_loop` task in
`server.py` calls `scan_commodities_paper()` ~every 60 s. **Never wired to live
orders** — the reused signal backtests negative on index/stock futures; this is a
forward-record lane. State: `memory/commodity_state.json`; closed trades:
`memory/commodity_journal.jsonl`.
