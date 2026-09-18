# TA-Lib + backtrader strategies — 2026-09-18

Richard asked to actually use the newly-added `TA-Lib` and `backtrader`
libraries (previously installed, untouched) to backtest and build new
strategies, on both India and crypto. Two strategies built
(`scripts/talib_strategies.py`), run through a real backtrader `Cerebro`
engine (`scripts/backtest_talib_backtrader.py`) against real historical
data, costed with this platform's own already-trusted charge functions —
no new cost model invented.

## The two strategies

- **`talib_candle`** — TA-Lib's built-in candlestick-pattern recognisers
  (engulfing, hammer, shooting star, morning/evening star, harami, 3-line
  strike, piercing, dark cloud cover, 3 white soldiers, 3 black crows) fired
  at a real N-bar swing support/resistance level, filtered by trend
  (price vs EMA50). Genuinely untried here before — far more patterns than
  the hand-rolled `index_ai.strategies.candlestick_patterns`.
- **`talib_macd_stoch`** — MACD histogram cross + Stochastic %K/%D reclaim
  from oversold/overbought, gated by ADX ≥ 20 (only trades a real trend).

Both exit through the same two-phase shape as everywhere else on this
platform (`memory/standard-trailing-stop-and-profit.md`): an ATR-based
hard stop, arming to breakeven-or-better once the trade is 1.5×ATR in
favor, then trailing 1×ATR behind the peak. Verified actually engaging —
not just theoretical — e.g. BANKNIFTY/`talib_macd_stoch`: 50 of 86 exits
were "trailing profit," not hard stops, with realistic 2–68 hour holds.

## Methodology

- **India**: NIFTY/BANKNIFTY/SENSEX, spot-replay on 15m bars (resampled
  from the local 1m cache, 2017–2026 for NIFTY / 2021–2026 for
  BANKNIFTY/SENSEX; 730-day window used here), same futures-track-spot
  approximation `index_ai.strategies.futures.backtest` already uses. Real
  costs via `index_ai.charges.futures_round_trip_rupees` +
  `futures_slippage_rupees` — the same schedule every other India-futures
  backtest on this platform uses.
- **Crypto**: the 6 live-configured Delta perps, 1h bars, 120-day window,
  real Delta history via `crypto.delta.market_data.candles`. Real costs
  via `crypto.charges.round_trip_cost_usd` (real Delta taker fee + 18% GST
  + measured/fallback half-spread) and the same sizing convention
  (`crypto.sizing`, $50 margin/position, 20x) the live paper lanes use.

## Results

### India (730 days, 15m spot-replay)

| strategy | trades | net (₹) | win rate | by instrument |
|---|---:|---:|---:|---|
| `talib_candle` | 448 | **−539,172** | 46% | BANKNIFTY −267,425 (111, 42%) · NIFTY −84,699 (170, 52%) · SENSEX −187,048 (167, 43%) |
| `talib_macd_stoch` | 259 | **−183,559** | 50% | BANKNIFTY **+72,404** (86, 58%) · NIFTY −112,008 (81, 44%) · SENSEX −143,955 (92, 47%) |

### Crypto (120 days, 1h)

| strategy | trades | net (USD) | win rate | by symbol |
|---|---:|---:|---:|---|
| `talib_candle` | 50 | **−$122** | 42% | all 6 symbols negative |
| `talib_macd_stoch` | 58 | **−$144** | 53% | XRPUSD **+$26** (5, 80%) · every other symbol negative |

## Verdict

**Both strategies are net-negative on both markets.** Same shape as every
other intraday strategy tested on this platform (`strategy-findings.md`):
a win rate that isn't far from 50% doesn't clear real costs and stop-outs.
Higher win rate (`talib_macd_stoch`, 50-53%) than the candlestick strategy
(42-46%) but the average loser outweighs the average winner enough that it
still nets negative overall.

Two isolated bright spots — BANKNIFTY/`talib_macd_stoch` (+₹72,404, 86
trades, 58% win) and XRPUSD/`talib_macd_stoch` (+$26, 5 trades, 80% win) —
are flagged, not claimed as edges. This platform has been burned before by
a single positive instrument inside an overall-negative test turning out to
be noise (the 3-day-hold sweep spike in `strategy-findings.md`, whose
neighbours flipped sign). 86 trades is a real sample size worth another
look; 5 trades is not.

**Do not re-test these as-is.** If Richard wants to push further: the
honest next steps are (a) re-measure `talib_macd_stoch` on BANKNIFTY alone
over a longer/different window to see if the positive result holds up out
of sample, or (b) try tightening the entry filters (e.g. require the
candlestick pattern to also be in the ADX-confirmed-trend direction) —
but per the standing lesson here, tightening an already-tested signal has
never turned a loser into a winner on this platform, only reduced trade
count.
