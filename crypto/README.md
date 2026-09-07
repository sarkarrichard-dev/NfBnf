# Crypto section — Delta Exchange India

A **separate section** of the platform: its own broker (Delta Exchange India),
its own `.env` keys (`DELTA_*` / `CRYPTO_*`), its own journals
(`memory/crypto_*`). Shares only the Telegram helper and the `.env` writer with
the index code.

**Status: paper only.** Phases 1–3 are built and wired in behind
`ENABLE_CRYPTO_PAPER` (default off). Live order placement (Phase 4,
`crypto/executor.py`) is not built — it will have its own two-lock arming,
separate from the index `arm_live_trading`.

- **Broker:** Delta Exchange India (FIU-registered, INR wallet — *not* Delta
  Exchange Global; different API host, different product ids).
- **Products:** BTC / ETH **perpetual futures** to start. Options are a later
  phase (they need a price proxy for backtesting, like the index options side).
- **Sizing:** by capital, not lots. `CRYPTO_DEPLOY_USD` (min $100) at
  `CRYPTO_LEVERAGE` (default 3x, capped at the product max) →
  `crypto/sizing.py` derives whole contracts, wallet-guarded to 90% of the
  bankroll.
- **Currency:** the wallet balance comes back from Delta already carrying
  `balance_inr` per wallet, so USD→INR is Delta's own number, not a hardcoded 88
  (fallback `CRYPTO_USDINR` only when there are no credentials).

## Layout

```
crypto/
  config.py          DELTA_* / CRYPTO_* env, CryptoSettings
  delta/client.py    signed REST (HMAC-SHA256, ported from the OpenAlgo connector)
  delta/products.py  perp contract master (product_id, contract_value, tick, min size)
  delta/market_data.py  ticker / candles (chunked) / l2 depth / resample
  session.py         the "crypto day": 18:00–23:00 IST for 6 PM, UTC date for Ichimoku
  sizing.py          $-deploy + leverage -> whole contracts
  charges.py         Delta fee + GST + MEASURED half-spread (samples the l2 book)
  journal.py         memory/crypto_journal.jsonl + crypto_state.json
  notify.py          crypto Telegram messages (USD + INR)
  strategies/
    ny_n_break.py    the "6 PM" strategy, ported from ny_n_break.pine
    ichimoku.py      TK-cross entry + cloud-reentry exit (reuses index_ai math)
    indicators.py    EMA, anchored VWAP, closing-basis pivots
  lanes.py           scan_crypto_paper() — the paper loop, one asyncio task in server.py
  api.py             /api/crypto {status, health, contracts, positions, journal, day, config, credentials}
  backtest.py        replay both strategies over real Delta candle history
```

`strategies/ny_n_break.pine` stays as the reference spec for the port. The
Delta protocol reference is the vendored OpenAlgo connector at
`reference/openalgo/broker/deltaexchange/` — check it before touching any
signing / endpoint / payload detail (same rule CLAUDE.md states for Dhan).

## The two strategies

**6 PM / NY N-Break** — 5-minute bias (EMA25 + day-anchored VWAP), arm off the
most recent on-side swing high/low, enter on the closing-basis re-break, exit on
the 15-minute opposite-N / hard SL / 23:00 session close. Max 3 trades per
session. The 18:00–23:00 IST window also gives the lane a daily boundary for
journaling and the Telegram summary.

**Ichimoku** — runs 24/7 on 1h. Entry: Tenkan crosses Kijun in the trade's
direction **and** price is on the right side of the Kumo **and** the forward
cloud agrees. Exit: `index_ai.strategies.ichimoku.cloud_reentry_exit` (price
back into the cloud) or hard SL.

They run as **independent lanes** — never share a position; both long BTC is two
journal rows, capped by `CRYPTO_MAX_CONCURRENT`.

## Cost model (Phase 3)

`crypto/charges.py` = Delta taker/maker fee + 18% GST on the fee + a half-spread.
The half-spread is **sampled from the live l2 book on every paper scan**
(`memory/crypto_spread_samples.jsonl`); once there are ≥ 30 samples the median
measured spread replaces the per-asset bps fallback. This is the index-side
lesson applied verbatim (`memory/strategy-findings.md`): a friction number that
decides the answer must be measured, not assumed.

Not modelled (tax-return items, not per-trade costs): the Indian VDA tax on
crypto gains (30% + 1% TDS).

## ML decision (2026-09-07)

The pinned direction said "the ML layer is shared with the index side." On
building it out, that is deferred: `index_ai/brain/features.py` is a 23-field
vector built around CPR width / TC-BC distances / `is_banknifty` — there is no
room for crypto signals (Ichimoku distances, VWAP distance, session phase) and
forcing crypto rows in would need a venue dimension that changes the dataset
shape for the index model too.

**Decision: a separate crypto model, later.** For now every closed crypto trade
is journalled with a superset `features` dict (`venue`, `is_btc`, strategy flag,
`side_long`, `atr_pct`, `ret_20_pct`, `entry_hour_utc`, and per-strategy
distance features) so no data is lost. Training a `crypto/brain/` model is a
follow-up once there are enough live rows — the same "~200 rows can't learn it"
constraint as the index side, and the perps backtest can pre-train relationships
(real prices, unlike the index BS-proxy) when that time comes.

## Not done

- **Options** (BTC/ETH CE/PE) — later phase; needs a pricing proxy for backtest.
- **Live order placement** — Phase 4, `crypto/executor.py`, separately armed,
  needs the server's egress IP whitelisted on the Delta key (SEBI static-IP
  mandate applies to Delta too).
- **Delta WebSocket feed** — REST polling is enough for 5m/1h strategies.
- **Multi-tenant credentials** — the platform-wide constraint, not solved here.
