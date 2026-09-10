# TMA Phoenix — reconstructed spec

**Source:** *"The Arty" – The Moving Average Official Indicator* (display name
**"The Phoenix 1.0 🔥"**, also "TMA - Phoenix Algo"), a **protected / closed-
source** Pine script by `PhoenixBinary` / `PhoenixBinary-Reboot` on TradingView,
built to package the method taught by **Arty** ("The Moving Average"). Free to
use, source locked. Last substantive release **2021-09-18** (v1.04).

This spec is reverse-engineered from the **public inputs, the plot list, the
author's own component description, and the release-note history** — observed on
Richard's `ETHUSD.P 5m` chart. It is an independent description, not a copy.

`tma_phoenix.py` is a from-scratch Python port of the *mechanical core* below.
Port to the live lane only after it clears a real Delta backtest — see the
caveats at the end.

---

## What it is

A **discretionary confluence dashboard**, not a mechanical system. Three parts:

1. a **Smoothed-MA (SMMA) ribbon + cloud** — the trend backdrop;
2. **two reversal candlestick patterns** — engulfing ("Big Ass Candles") and
   3-Line-Strike — that print ▲ / ▼ marks;
3. an **ATR bracket calculator** — a table showing Enter / Break-even / Target /
   Stop for a long and a short, always populated regardless of any signal.

**The patterns are not gated by the ribbon in code.** A human following Arty's
method is expected to take a pattern only when it agrees with the cloud and sits
at an MA. The port makes that filter explicit.

## Inputs (Richard's configured values)

| Group | Input | Value |
|---|---|---|
| Moving averages | type | **Smoothed (SMMA / Wilder)** |
| | source | close |
| | timeframe | same as chart |
| | MA1 / MA2 / MA3 / MA4 lengths | **21 / 50 / 100 / 200** |
| | MA4 (200) draw mode | **Dynamic** (colour flips with its own slope) |
| Trend cloud | mode | Small Dynamic, opacity 85 |
| Pattern 1 — "Big Ass Candles" | = **engulfing candles**; bull/bear/both/hide | **Both** |
| | Strict/Not | **Strict 21** |
| Pattern 2 — **3 Line Strike** | bull/bear/both/hide | **Both** |
| | Strict/Not | **Strict** |
| Pattern 3 — "Five Min Scalp" | | **Hidden** |
| Risk management | Stop / B-E / Target (× ATR) | **1.5 / 0.5 / 1.5** → 1 : 1 R:R |
| Backtesting | Session 1 / 2 | NY / London 08:00–16:00 (visual only, off here) |

## Components (author's description, condensed)

**SMMA ribbon** — four Wilder-smoothed MAs of the close:

- **21** — white, closest to price, top of the "small cloud"
- **50** — green, bottom of the small cloud
- **100** — yellow, *optional* ("only used occasionally in Arty's teachings")
- **200** — red, part of the "large cloud"; **dynamic colour** = "who is in
  control short term"

**Clouds** — a fill 21↔50 (solid or dynamic), and a large fill 200↔price for a
quick direction read. Purely visual.

**Candlestick patterns** (the signal marks):

- **"Big Ass Candles" = engulfing** — a bar whose body engulfs the prior bar's
  body, in the opposite direction. "Strict" tightens the definition (the release
  notes mention a "Big Ass Candle formula" fix — the strict form compares body
  and range, not just the close).
- **3 Line Strike** — a reversal pattern: three candles trending one way, then a
  single candle that closes beyond the start of the run (engulfs all three).
  "Strict" requires the three candles to be cleanly monotonic.

Alerts can fire on bull, bear, or the two combined.

**Risk-management table** — Enter / B-E / Target / Stop, computed as
`price ± multiplier × ATR(14)`. With 1.5 / 0.5 / 1.5 the target distance equals
the stop distance (1 : 1), and break-even sits at +0.5 ATR.

**Sessions** — draw up to two trading windows so you only judge signals during
hours you actually trade. Shading only, not a signal filter.

## The mechanical core (what the port implements)

**Trend / bias** (`ribbon`)
1. SMMA(21), SMMA(50), SMMA(100), SMMA(200) of the close.
2. `up` = `21 > 50 > 200` **and** the 200 SMMA rising over `slope_lookback`
   bars **and** price above the 200. `down` mirrored. Else `flat`.
   (100 is an optional extra stack condition — `require_ma3`.)

**Trigger** (on the last closed bar)
3. **bullish engulfing** — last bar up, prior bar down, last close > prior open,
   last open ≤ prior close; *strict* also needs last body ≥ prior body.
4. **bullish 3-line-strike** — bars −4…−2 all down (strict: strictly lower
   closes), last bar up and closing above bar −4's open.
5. Bearish mirrors of both.

**Entry**
6. Flat, `ribbon ≠ 0`, a trigger fires **in the ribbon direction**, the signal
   bar is not oversized (`> big_candle_atr × ATR`), and — if `near_ma_atr > 0` —
   the signal bar's low/high is within `near_ma_atr × ATR` of the 50 or 200
   SMMA (Arty's "take it at an MA").

**Exit** — the ATR bracket, faithful to the indicator:
7. On entry, snapshot `ATR`. Stop = entry ∓ `stop_atr × ATR`;
   Target = entry ± `target_atr × ATR`.
8. Once price reaches +`be_atr × ATR` in favour, move the stop to entry
   (break-even).
9. Exit on stop or target. The shared P&L trailing engine runs underneath as a
   100×-leverage liquidation backstop.

## Caveats before trusting a signal

- **Not a system.** The indicator is a visual aid for a discretionary trader.
  "Pattern fires → take it" is not how it's meant to be used, and the port's
  ribbon + near-MA filters are an interpretation, not the original logic.
- **Nearly the same shape as AK Roxx Pro** (MA ribbon + candlestick trigger +
  ATR bracket), which **failed its Delta backtest with no edge**
  (`crypto/strategies/ak_roxx_pro.md`). An engulfing / 3LS trigger at 1 : 1 R:R
  is *less* selective, so the prior is that this also bleeds after fees.
- **Friction.** Every 5-minute crypto config measured on this platform is
  net-negative after real costs (`memory/strategy-findings.md`).
- The guessed constants (strict thresholds, slope lookback, near-MA distance,
  big-candle K) each move the result — fit them on Delta 5m history for
  BTC / ETH / SOL and report the sweep.

## Backtest result — it did not clear (2026-09-09)

`crypto/strategies/tma_phoenix.py` is the port; `crypto/backtest.py` runs it
(`python -m crypto.backtest --days 60 --strategy tma_phoenix`). Delta 5m history,
60 days, BTC/ETH/SOL, entries on the closed signal bar, ATR bracket exit.

**Net, after Delta fees (default config):** **−$4,464 over 2,011 trades**, 26%
win (BTC −$1,743 · ETH −$461 · SOL −$2,259). Avg win $3.76 vs avg loss −$4.32 —
an *inverted* payoff at a 26% hit rate, ~33 trades/day across three coins. The
engulfing / 3-line-strike trigger fires constantly on the 5m frame and the SMMA
ribbon filter barely thins it.

**Gross, costs zeroed:** BTC +$35 (+$0.05/trade), ETH +$29 (+$0.05/trade),
SOL −$159 (−$0.25/trade) → total ≈ **−$95** — *no gross edge*, flat-to-noise.
Same verdict as AK Roxx Pro and every other 5-minute crypto config on this
platform (`memory/strategy-findings.md`): the reversal-candle signal has no edge
to begin with, so friction is not even the deciding factor.

Kept in the tree as documented-dead (like `ak_roxx_pro.py`):
wired to `crypto/backtest.py` only, **not** to `crypto/lanes.py`, no
`CRYPTO_TMA_PHOENIX_ENABLED` flag. The 1:1 ATR bracket calculator is the one
reusable idea, for a signal that is already predictive. Re-running the sweep on
this trigger is not a pending task.
