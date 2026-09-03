# Crypto lane — parking lot

**Nothing in here is wired into the running system.** No imports, no scanner
stage, no `.env` keys, no orders. It is a holding area for the crypto section
so the pieces stop living in chat logs and TradingView tabs.

Direction (set 2026-09-02/03): crypto is a **separate section** of the platform
— its own logs, strategies and API keys, against a different broker — while the
ML / analysis layer is shared with the index side.

- **Broker:** Delta Exchange India (the FIU-registered Indian entity, INR
  wallet — *not* Delta Exchange Global; different API host, different product
  ids, credentials are not portable between them).
- **Products:** BTC / ETH perpetual futures and options.
- **Sizing:** by capital, not lots. The operator says how much money to deploy;
  the system derives contracts and margin from the per-asset contract size.
  Every asset has a different contract value — this must be per-instrument
  config, never a shared constant.
- **Currency:** contracts are quoted in USD, the wallet is INR. Two *separate*
  mechanisms that compose: the FX reference rate (≈88 INR/USD) and the margin
  requirement (contract size × mark price × margin %). Keep them apart in code.

## What is parked here

| File | What it is | Status |
|---|---|---|
| `strategies/ny_n_break.pine` | TradingView Pine v6 strategy — "NY N-Break", BTC/ETH perps, 5-min. The 6 PM strategy, mechanised. | Reference implementation; not ported to Python |
| `strategies/ichimoku.py` | Snapshot of `index_ai/strategies/ichimoku.py` | **Snapshot only** — canonical copy is still in `index_ai/`, which `backtest.py` imports. Do not delete that one. |

### `ny_n_break.pine` in one paragraph

Trades only inside the New York session expressed in IST (**18:00–23:00**, hence
"6 PM"), on the 5-minute chart. Bias filter is EMA25 + VWAP: price must close
above *both* (long) or below *both* (short). Entry is deliberately not a chase —
after the bias is set it waits for the first swing high to form, a retrace, then
a **re-break of that swing high on a closing basis** (the "N" shape); the short
side mirrors it (inverted N). Once in, EMA/VWAP are ignored. The exit watches
the **15-minute** structure and closes when the opposite pattern prints — for a
long, a 15-min swing *low* getting broken. Max 3 trades per session, optional
hard stop-loss %, optional force-close at session end.

## Before any of this goes live

Open questions, in rough order of "will cost money if wrong":

1. **Delta India contract specs** — contract size for BTCUSD / ETHUSD perps and
   for options, tick size, min order qty, max leverage. A wrong contract size
   silently sizes the position 10× off; it does not error.
2. **Margin formulas** — initial vs maintenance, isolated vs cross, liquidation
   price, and funding-rate mechanics for perps. Needed for the
   "deploy ₹X → N contracts" sizer.
3. **Order size is an integer count of contracts**, not a fractional coin
   quantity — the sizer has to floor to whole contracts.
4. **Cost model** — Delta maker/taker fees, GST on fees, and the Indian VDA tax
   treatment (30% + 1% TDS) for crypto derivatives. This is the crypto analogue
   of `index_ai/charges.py`, and the index-side lesson applies: an assumed
   friction number that decides the answer belongs in a measurement path, not a
   constant.
5. **Auth** — Delta uses a static API key + HMAC-SHA256 signature over
   `method + timestamp + path + query + body`, with a ~5 second signature
   expiry, so the machine clock must be NTP-synced. No daily-token dance, so
   none of `dhan_auth.py` carries over.

## The architectural mismatch to design around

The index code assumes a **session**: `market_clock.py` hard-codes 9:15–15:30
IST, a 9:00 pre-open brief, a 15:10 hard square-off and a once-a-day EOD report.
**Crypto is 24/7** — no close, no square-off, no expiry roll for perps. The
session scaffolding cannot be reused as-is.

The NY N-Break strategy actually helps here: it imposes an artificial daily
window (18:00–23:00 IST), which gives the crypto lane something to anchor a
"day" to for journaling, the day review and the Telegram summary.

Second thing to design around: `brain/features.py` has `is_banknifty`,
`is_sensex` and `cpr_width_pct` baked into its 20-field vector. Sharing the ML
layer means either adding an asset/venue dimension to that vector, or running a
separate model per venue. Decide before the first crypto trade is journaled,
because the dataset shape is hard to change afterwards.
