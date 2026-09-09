# AK Roxx Pro — reconstructed spec

**Source:** "AK Algo Buy and Sell Signals" (display name *AK Roxx Pro*),
invite-only Pine script by `Ahmad_Ali_Khan` on TradingView
(`tradingview.com/v/nylb7uG6`). Source code is locked. This spec is reverse-
engineered from the **public inputs, the plot list, the author's release notes,
and observed behaviour on ETHUSD.P 5m** — it is an independent description, not a
copy of the protected code. Numbers marked *(guess)* are not recoverable from the
outside and need to be fit by backtest.

`ak_roxx_pro.pine` is a from-scratch Pine v6 implementation of everything below.
Port to `crypto/strategies/ak_roxx_pro.py` only after it clears a real Delta
backtest — see the caveats at the end.

---

## What it is

A discretionary **price-action + support/resistance + trend** signal generator.
Fixed **1:2 reward:risk**. Trend-riding: once a signal is active it holds until the
trailing stop is hit — no opposite signal in between. Optional scalping mode.
Multi-symbol screener. Not a mechanical edge — a confluence dashboard.

## Inputs (the complete configurable set)

| Input | Value | Meaning |
|---|---|---|
| Enable AK Roxx Updated | on | master switch for the current logic version |
| `A` | 21 | fast length of a 3-MA ribbon (Fibonacci) |
| `B` | 34 | mid length |
| `C` | 55 | slow length |
| Require slope | on | the ribbon must be sloping in the trade direction, not flat |
| Require price beyond ALL 1H CPR levels | on | price must be fully outside the 1-hour Central Pivot Range (above TC or below BC) — inside ⇒ *NO TRADE ZONE* |
| Show 1H CPR & Classic Pivots | on | draw the 1H CPR (Pivot/BC/TC) and the classic floor pivots R1–R3 / S1–S3 |
| Show CPR values label | on | the CPR values table |
| Enable Screener | off | run the signal across a watchlist without opening each chart |

Everything else (the volume-S/R lookback, the big-candle threshold, the trailing-
stop distance, the MA type) is **hard-coded in the script** — not exposed.

## Components (from the Style tab — every plot the script draws)

- **Bar Color** — candles tinted by the trend state (bull/bear/neutral).
- **Pivot P, TC, BC** — the **1-hour CPR**. `Pivot = (H+L+C)/3`, `BC = (H+L)/2`,
  `TC = 2·Pivot − BC`, computed from the **previous completed 1H bar**.
- **R1 R2 R3 S1 S2 S3** — classic floor pivots (drawn, hidden by default).
- **Buy Signal / Sell Signal** — the entries (label below / above bar).
- **Buy Exit Line / Sell Exit Line** — the **trailing stop** after entry (the
  red line that follows price). Distance *(guess: ATR-based or the mid MA)*.
- **Big Candle Highlight** — flags an oversized signal candle. Author's rule:
  *"avoid trades if the signal candle is too big, or wait for a retracement."*
- **Resistance Holds / Support Holds** — ◆ marker when price tests a level and
  **rejects** it (reversal cue).
- **Resistance as Support Holds / Support as Resistance Holds** — ◆ marker when a
  **broken** level is retested from the other side and holds (continuation cue).
- **Boxes** — the `Vol: ±NNNNN` zones: a support/resistance band built from the
  last *N* candles, tagged with the **net volume** (buy − sell) that formed it.
  Green = demand, red = supply.
- **Pane labels** — the *"NO TRADE ZONE · Awaiting Signal · [UPDATED MODE]"* box.
- **Tables** — the CPR values.

## Evolution (author's release notes)

| Date | Change |
|---|---|
| 2024-12 | price-action Buy/Sell, fixed **1:2** SL/target |
| 2025-02 | optional **Scalping Mode** (tuned for index options) |
| 2025-04 | **trend-ride**: while a signal is active, no new signal until the trailing SL is hit |
| 2025-07 | multi-symbol **Screener** |
| **2025-11** | **volume-based S/R** — levels from the volume of the last several candles; *don't buy near resistance / sell near support* |
| **2026-07** | added the **hourly CPR** filter |

## The logic, assembled

**Trend engine**
1. Three MAs of length 21 / 34 / 55 (*type a guess — EMA or HMA*).
2. `trendUp`  = `MA21 > MA34 > MA55` **and** all three rising over the last *k*
   bars (`Require slope`). `trendDown` mirrored. Else `neutral`.
3. Candles are coloured by this state.

**S/R map**
4. Recent swing highs = resistance, swing lows = support (pivot lookback
   *(guess ~10–20)*). Each level carries the cumulative volume delta of the bars
   in its zone → the `Vol:` tag; sign shows demand vs supply.
5. `nearResistance` = price within *(guess ~0.15–0.3 %)* of the nearest swing
   high above; `nearSupport` mirrored.

**1H CPR gate**
6. From the previous 1H bar compute Pivot / BC / TC.
7. `beyondCPR_up`   = `close > TC(1H)`; `beyondCPR_down` = `close < BC(1H)`.
   Inside the range ⇒ **NO TRADE ZONE**, no signals.

**Big-candle gate**
8. `bigCandle` = bar range `> K · ATR` *(guess K ≈ 1.8–2.5)*. On a big signal
   candle: suppress the signal, or wait for a retrace into the level.

**Entry (on bar close only)**
9. **BUY** when: `trendUp` **and** `beyondCPR_up` **and** not `nearResistance`
   **and** not `bigCandle` **and** no position open **and** (in Scalping Mode,
   an additional shorter trigger). SL and target set at **1 : 2**.
10. **SELL** = mirror.
11. After entry: ignore all new signals until the trailing **Exit Line** is hit
    (or SL / target). One position at a time.

**Exit**
12. Trailing stop line that ratchets with price; fixed 1:2 target; hard SL.

## Caveats before trusting a single signal

- **Repaints.** The volume-S/R levels and the swing pivots rebuild as bars form,
  so historical BUY/SELL marks are partly hindsight. Backtest with
  `barmerge.lookahead_off`, confirmed pivots only, and entries on close.
- **Not moderator-reviewed** (invite-only). No independent audit of the logic.
- **Friction.** Every 5-minute crypto config measured on this platform is
  net-negative after real costs (`memory/strategy-findings.md`). "Signal fires →
  take it" almost certainly loses. The value here is the *ideas* — the **1H-CPR
  breakout gate** and the **volume-weighted S/R avoidance** — as filters layered
  onto a signal that is already predictive, not as a standalone system.
- The guessed constants (MA type, pivot lookback, near-level %, big-candle K,
  trail distance) each move the result — fit them on Delta 5m history for BTC /
  ETH / SOL and report the sweep, don't hard-code a number.

## Backtest result — it did not clear (2026-09-09)

`crypto/strategies/ak_roxx_pro.py` is the port; `crypto/backtest.py` runs it
(`python -m crypto.backtest --days 120 --strategy ak_roxx_pro`). Replayed on
Delta 5m history, 60 days, BTC/ETH/SOL, entries on close, confirmed pivots,
previous completed 1H bar for the CPR — the honest setup.

**Net, after Delta fees (default config):** −$7,972 over 3,562 trades, 32% win
(BTC −$2,081 · ETH −$964 · SOL −$4,927). Every point of the `near_pct` ×
`require_beyond_cpr` sweep loses; best was `near_pct=0.30, beyond_cpr=on` at
−$6,124 / 2,765 trades. Turning the 1H-CPR gate **off** makes it strictly worse
(more entries, more bleed) — so the CPR gate helps at the margin but nowhere
near enough.

**Gross, costs zeroed:** BTC +$168 (+$0.19/trade), ETH −$38 (−$0.03/trade),
SOL −$74 (−$0.05/trade) — i.e. **no gross edge**, flat to slightly positive on
BTC and noise on the rest. This is the same verdict as every other intraday
config on this platform
(`memory/strategy-findings.md`): the signal has no edge to begin with, so
friction is not even the issue here.

The module stays in the tree as documented-dead (like `ema_pivot.py`): wired to
the backtest, **not** wired to `crypto/lanes.py`, no `CRYPTO_AK_ROXX_ENABLED`
flag. The reusable idea — a higher-timeframe CPR/pivot breakout gate layered on
a signal that is *already* predictive — is preserved here for when such a signal
exists. Re-running the sweep on this signal is not a pending task.
