# AK Roxx Pro — spec (rebuilt from the live portal, 2026-09-10)

**Source:** the "AK Roxx Alpha" trading portal (`portal.akroxxtech.com`) — a
TradingView-charting-library front end with the AK Roxx signal set built in as
four custom studies. Richard has a paid seat. The original TradingView Pine
script ("AK Algo Buy and Sell Signals" by `Ahmad_Ali_Khan`) is invite-only and
locked, **but the portal recomputes the same signals in browser JavaScript**
(`assets/js/chart-v2/indicators.js` + `custom-study.js`), and the author's own
comments there say that code is *"ported verbatim from pages/chart.php's math …
verified bar-for-bar"*. This spec is an independent description of the **logic
and parameters** read off that running implementation and the study Inputs UI —
not a copy of the protected Pine source.

The earlier version of this file (and `ak_roxx_pro.py`) was reverse-engineered
from the public inputs + release notes only, and **got the core wrong** (it used
a 21/34/55 EMA ribbon and guessed constants). The real logic is below. The
2026-09-09 backtest of −$7,972 was run on that wrong logic; it does not tell us
anything about the real signal.

---

## The four studies

| Study | Portal name | Role |
|---|---|---|
| **Alpha 1** | "AK Roxx" | the main buy/sell signal — an 8-condition confluence gate |
| **Alpha 2** | "5m Trend Strategy" | an independent second signal — channel break + trend filters |
| **Alpha CPR** | — | draws the hourly Central Pivot Range box (used as a gate by both signals) |
| **Alpha Support Resistance** | — | draws the volume-weighted S/R zones (a display gate) |

The status bar shows both engines at once, e.g. `A1: SELL | A2: SELL · SL 77445.39`.
When both agree it is treated as the strongest read. Adding "Alpha Support
Resistance" / "Alpha CPR" to the chart is the on/off switch for those overlays.

---

## Alpha 1 — "AK Roxx" (the main signal)

All computed on the **5-minute** frame, on **closed bars only**. Default Inputs
(confirmed in the study's Settings → Inputs, and in the chart legend
`8 8 7 14 13 21 34 20 2 1 25 1 2 20`):

| Input | Default | Meaning |
|---|---|---|
| AK Channel Length 1 / Length 2 | 8 / 8 | the two lengths of the "AK Channel" (`upC` upper band, `loC` lower band) |
| Short EMA | 7 | `EMA(close, 7)` |
| Long EMA | 14 | `EMA(close, 14)` |
| PEMA A / B / C | 13 / 21 / 34 | the "PEMA" ribbon — three EMAs of **hlc3** (typical price) |
| S/R Lookback | 20 | swing lookback for the S/R zones |
| S/R Vol Len | 2 | bars each side for the volume-delta tag |
| S/R Box Width | 1.0 | ATR multiple for zone height |
| CPR Proximity | 25 | % of the CPR band's own width — a signal within this of the band is "high-probability" (B+/S+) |
| T1 / T2 | 1.5 / 2 | reward multiples of the signal's own risk (1:1.5, 1:2) |
| Big-candle avg | 20 | trailing window for the big-candle / bar-colour reference range |

### Entry — `rawBuy` fires when **all eight** are true (mirror for `rawSell`)

1. **`macUp`** — both AK-Channel bands are rising vs the prior bar (`upC > upC[1]` **and** `loC > loC[1]`). This is the "moving-average channel" trend read.
2. **close > `upC`** — price closed above the upper channel band.
3. **close > close[1]** — price closed above the previous bar's close.
4. **EMA7 > EMA14**.
5. **EMA7 rising** (`EMA7 > EMA7[1]`).
6. **EMA14 rising** (`EMA14 > EMA14[1]`).
7. **CPR gate** — either there is no completed hourly CPR yet, **or** close is above the hourly CPR's top level (`cprMax` / TC). (`rawSell`: close below `cprMin` / BC.) This is the "price must be fully outside the 1H CPR — inside is the NO TRADE ZONE" rule.
8. **PEMA ribbon bullish** — the 13/21/34 ribbon is **stacked** (fast > mid > slow) **and sloping up**.

### Signal state machine — verified line-for-line 2026-09-11

Read straight from `indicators.js::computeAlpha1Signals`:

```
if (rawBuy  && !buyActive && !sellActive) { buyActive  = true;  sellActive = false; }
if (rawSell && !sellActive && !buyActive) { sellActive = true;  buyActive  = false; }
if (buyActive  && c.close < loC) buyActive  = false;   // <-- the exit
if (sellActive && c.close > upC) sellActive = false;   // <-- the exit
// the marker fires only on the bar buyActive/sellActive turns on
```

- **Enter** on the first bar `rawBuy` is true while flat. It is *not* edge-triggered on `rawBuy` — but since condition 2 (`close > upC`) fails on any bar where you'd have just exited (`close < loC < upC`), you can't re-enter until price reclaims the channel, so no churn.
- **Exit — the entire exit.** Long: the first bar `close < loC` (the **current** `SMA(low, 8)`, not a ratcheted value). Short: `close > upC`. **No target. No fixed stop. No P&L trail.** The position rides the channel.
- **T1 / T2 / SL lines** drawn on the chart come from a *separate* `computeTargets` annotation (1.5R / 2R off the entry-to-band distance) — display only, they do not close the position. The author's spoken commentary about "target 77,480" is his discretionary layer, not the mechanical engine.

### B+ / S+ (high-probability variant)

Same signal, re-labelled when it lands **at the hourly CPR** — inside the BC..TC
band, widened each side by `CPR Proximity`% **of the band's own width** (default
25). Band-relative on purpose: BTC's median hourly CPR band is ~25 points, so a
percent-of-price tolerance would mark almost everything. On BTC/5m over a week,
proximity 0 marks ~20% of signals, 25 marks ~29%, 100 marks 59%.

---

## Alpha 2 — "5m Trend Strategy" (the second signal)

Config (`A2_CFG`): `upperLength: 15, lowerLength: 15, chopMax: 38.2,
stFactor: 3.0, stAtrLen: 10`. Choppiness length **14**.

### Entry — `buyEdge && chopOK && cprBuyOK && stBuyOK` (mirror for sell)

- **`buyEdge`** — a *fresh* break of the 15-bar channel edge (`brkBuy` true now, false on the prior bar). Donchian-style break.
- **`chopOK`** — Choppiness Index(14) **< 38.2** (trending, not chopping).
- **`cprBuyOK`** — there is an hourly CPR **and** close is above its top.
- **`stBuyOK`** — Supertrend(factor 3.0, ATR 10) is up **and** a long isn't already open (one trade per trend).

Same trailing-stop exit shape as Alpha 1.

---

## Support / Resistance zones (`calcSRLevels`)

Defaults: lookback 20, volLen 2, boxWidth 1.0.

Confirmed swing highs = resistance, swing lows = support. Each swing carries the
**net volume delta** (buy − sell) of the bars in its zone → the `Vol: ±NNNNN`
tag (green demand / red supply). Zone height is ATR-based. A zone is "broken"
once price later closes past it. Only the **3 most recent unbroken** supports and
resistances are kept. The indicator's guidance: *don't buy into resistance,
don't sell into support*.

## CPR zones (`calcCPRZones`)

Hourly Central Pivot Range from the **previous completed 1-hour bar**:
`P = (H+L+C)/3`, `BC = (H+L)/2`, `TC = 2P − BC`. Note `TC` lands *below* `BC`
whenever the prior hour closed under its own midpoint — order them before
comparing. Drawn as a box spanning the current hour.

---

## Status (2026-09-11 — faithful port)

`ak_roxx_pro.py` now matches the portal's `computeAlpha1Signals` exactly:

- 8-condition `rawBuy` / `rawSell` (above), fired the first flat bar it's true.
- **Exit = close back through the far band** (`close < SMA(low,8)` for a long),
  same as the real indicator — no ratchet, no target tied to the signal
  itself. The earlier ports had a ratchet/target grafted onto the *entry*
  logic and their −$8k / −$2k backtests are **void**. As of 2026-09-22 the
  shared crypto P&L trail (`crypto/strategies/trailing.py`, the same backstop
  every other crypto strategy runs) sits underneath this exit — whichever
  fires first wins. **Correction (same day, caught in review):** at the
  current tuned trail settings (~0.8% price move for the initial stop) this
  strategy's own 1h channel band (an 8-hour swing stop) almost never wins
  the race — the trail fires first on most real moves, which means this
  is *not* the rare disaster-only backstop it was meant to be; it's become
  ak_roxx_pro's de facto primary exit. Open question for Richard: give this
  strategy its own, much wider trail so the channel band decides again, or
  drop the trail from it entirely and go back to channel-only. Not yet
  decided — don't assume either resolution.
- Optional `require_alpha2_agree` (default off) — extra filter, Alpha 1 itself
  doesn't use Alpha 2.
- `SEARCH_SPACE["ak_roxx_pro"]` — `require_beyond_cpr`, `require_alpha2_agree`,
  `upper_len`, `lower_len`.
- Wired as a **paper** lane (`CRYPTO_AK_ROXX_ENABLED`, default on); dashboard
  card + toggle + status.

**Timeframe = 1h** (`AkRoxxConfig.timeframe`, env `CRYPTO_AK_ROXX_TF`). The
`close < SMA(low,8)` exit is a 40-min leash on 5m (churns, −$2.3k/45d) but 8
hours of lows on 1h — a real swing stop. On 1h: ~2 trades/day, avg win 2× avg
loss, net −$376/90d (≈ flat, ~6 pts of win-rate short of breakeven). Closest any
crypto strategy has come. See `RESULTS.md`.

## What the port does *not* copy from the portal

The S/R-zone "don't buy into resistance" nudge, the bar-colour / big-candle
*highlight*, the B+/S+ CPR-proximity label, and the drawn T1/T2 lines are all
display features — they don't gate or close a trade. The one real simplification:
on a **live** entry the lane still attaches a wide Delta bracket stop from the
shared `TrailConfig` as a disaster backstop; the strategy's own exit is the
channel break.

**Caveat that still stands:** every 5-minute crypto config measured on this
platform is net-negative after real Delta costs (`memory/strategy-findings.md`).
The 8-condition gate is much stricter than the old port, so it should trade far
less — but "signal fires → take it" has never worked here. Treat this as a lane
to *watch forward*, the way the 20-stock futures lane is watched, not as a proven
edge. Optimising a no-edge signal does not create an edge.
