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

### Signal state machine (trend-ride)

- A signal fires only when `rawBuy` is true **and** neither a buy nor a sell is already active (`!buyActive && !sellActive`).
- Once active it **stays active** — no new signal, no opposite signal — until the trailing stop is hit. The trail is seeded at entry with the channel edge and ratchets with price. This is the author's "while a signal is active, no new signal until the trailing SL is hit".
- **T1 / T2**: risk = (signal-bar close) − (trail stop at entry); targets are 1.5× and 2× that, drawn as short two-candle lines.

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

## Status (2026-09-10)

Done:

- `ak_roxx_pro.py` re-ported to the real logic above — the 8-condition Alpha 1
  `rawBuy`/`rawSell`, the trend-ride state machine, the 1:`rr` target, plus an
  optional `require_alpha2_agree` gate that also computes the Alpha 2 direction
  (15-bar break + Choppiness(14) < 38.2 + Supertrend(3, 10)).
- Wired into `crypto/lanes.py` as a **paper** lane — `CRYPTO_AK_ROXX_ENABLED`,
  default on. Delta places no paper orders.
- In `crypto/ml/optimize.py`'s `SEARCH_SPACE["ak_roxx_pro"]` — `require_beyond_cpr`,
  `require_alpha2_agree`, `slope_lookback`, `rr`. `retune_all()` walk-forwards it
  nightly and *suggests* changes for approval; never auto-applies (ML guardrails).
- Dashboard: catalog card + lane toggle + status plumbing.

Pending:

- `python -m crypto.backtest --strategy ak_roxx_pro --days 120` on the corrected
  logic — record the net / trades / per-symbol here. The old −$7,972 is void.

## What the port does *not* copy from the portal

The trail is the crypto lane's shared **P&L-percent** engine (10% of margin at
100×), not the portal's channel-edge price stop — every crypto strategy exits
the same way. The S/R-zone "don't buy into resistance" gate and the bar-colour /
big-candle *highlight* are display features on the portal; only the optional
`big_candle_atr` skip is carried (default off).

**Caveat that still stands:** every 5-minute crypto config measured on this
platform is net-negative after real Delta costs (`memory/strategy-findings.md`).
The 8-condition gate is much stricter than the old port, so it should trade far
less — but "signal fires → take it" has never worked here. Treat this as a lane
to *watch forward*, the way the 20-stock futures lane is watched, not as a proven
edge. Optimising a no-edge signal does not create an edge.
