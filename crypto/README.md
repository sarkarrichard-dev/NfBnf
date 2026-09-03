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

A research pass on 2026-09-03 was **cut off partway through verification**, so
the notes below are split by how well-sourced they actually are. Re-run it (or
just read the Delta docs) before writing the broker client.

### Verified against docs.delta.exchange (3-of-3 adversarial votes)

- **API host is entity-specific.** India production is
  `https://api.india.delta.exchange`; India testnet is
  `https://cdn-ind.testnet.deltaex.org`; Global is `https://api.delta.exchange`.
  Keys created on one do **not** work on another.
- **Auth** — headers `api-key`, `timestamp`, `signature`, `User-Agent`, where
  the signature is a hex SHA256 HMAC over the prehash string
  `method + timestamp + requestPath + query + body`.
- **Signatures expire 5 seconds after creation**, measured at Delta's server —
  so the machine clock must be NTP-synced or every request fails with
  `SignatureExpired`. Requests also fail if the calling IP is not whitelisted or
  the key lacks Trading (vs Read Data) permission.
- **Orders** — `POST /v2/orders` with `product_id` or `product_symbol` (e.g.
  `BTCUSD`), `order_type` (`limit_order` / `market_order`), `side`, `size` in
  contracts, `limit_price`, `time_in_force`, `reduce_only`, `post_only`, and
  optional `bracket_stop_loss_price` / `bracket_take_profit_price`. Cancel is
  `DELETE /v2/orders`, edit `PUT /v2/orders`, batch `POST /v2/orders/batch`
  (capped at 50, `ioc` not valid in a batch).

Good news for the port: no daily-token dance, so none of `dhan_auth.py`'s
OAuth + TOTP + expiry machinery carries over.

### Unverified — believed true, but the verification pass never completed

- Order `size` is an **integer count of contracts**, not a fractional coin
  quantity, so the sizer must floor to whole contracts and coin exposure is
  `contracts × contract_size`.
- Timestamp is epoch **seconds** (not ms); body must be signed and sent as the
  identical byte string.

### Not answered at all — do these first, they cost money if wrong

1. **Contract specs** — contract size for BTCUSD / ETHUSD perps and for
   options, tick size, min order qty, max leverage, strike intervals, expiry
   cycle. A wrong contract size silently sizes the position 10× off; it does
   not error.
2. **Margin formulas** — initial vs maintenance, isolated vs cross, liquidation
   price, and funding-rate mechanics for perps. Needed for the
   "deploy ₹X → N contracts" sizer.
3. **The INR/USD reference rate** — where it comes from, how often it updates,
   whether there's a markup, and where the API exposes it.
4. **Cost model** — Delta maker/taker fees, GST on fees, and the Indian VDA tax
   treatment (30% + 1% TDS) for crypto derivatives. This is the crypto analogue
   of `index_ai/charges.py`, and the index-side lesson applies: an assumed
   friction number that decides the answer belongs in a measurement path, not a
   constant.

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
