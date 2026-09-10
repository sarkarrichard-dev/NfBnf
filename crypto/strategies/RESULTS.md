# Video-strategy evaluation — 2026-09-08

Three crypto-strategy videos Richard sent, ported to `crypto/strategies/` and
backtested over real Delta candle history with `crypto.charges` applied (Delta
taker fee 0.05% × 2 = 0.1% of notional round-trip + measured/fallback
half-spread). **The gate to enable one live: a stable positive walk-forward
out-of-sample net across ≥ 2 symbols.**

## The three strategies

| module | video | shape | timeframe |
|---|---|---|---|
| `ema_jaguar` | "A Simple 5-Minute EMA Strategy" | EMA(13)/EMA(34) crossover | 5m |
| `bb_reversal` | "Bitcoin & Crypto Scalping Strategy" (HOLD with Priyank) | Bollinger pierce + W/M reclaim | 5m |
| `vp_edge` | "Intraday Trading for BTC & ETH" (Theta Gainers) | session volume-profile value-area fade on a balanced (D-shape) profile | 15m |

## Backtest — video defaults, 45 days, BTCUSD / ETHUSD / SOLUSD

| strategy | trades | net USD | win rate | by symbol (net) |
|---|---:|---:|---:|---|
| `ema_jaguar` | 1117 | **−$1,808** | 23% | BTC −584 · ETH −214 · SOL −1010 |
| `bb_reversal` | 675 | **−$1,128** | 24% | BTC −427 · ETH −93 · SOL −608 |
| `vp_edge` | 0 | — | — | never finds a balanced-enough profile on real crypto |

`avg win ≈ $2.7`, `avg loss ≈ $2.9` for both 5m strategies — a ~1:1 payoff at a
~23% hit rate is a guaranteed bleed, and at 25/day (ema_jaguar) the fee drag
compounds it. `vp_edge` is the opposite failure: real crypto is almost never the
symmetric bell the D-shape rule needs, so it produces no signal.

## Auto-tune (`crypto/ml/optimize.py`)

Random search over the per-parameter grid, scored on rolling out-of-sample folds
with a neighbour-stability gate. **Every combo it tried was ineligible** (net
negative or too few trades) — `eligible: 0` for all three. Best combos:
`ema_jaguar` fast=21/slow=89 → still net −; `bb_reversal` bb_len=14/dev=2.5 →
net − (near-breakeven on a 20-day slice, −$1,128 over 45 days). The losses are
structural (5m scalp friction), not a parameter-tuning problem.
`tuned_params()` therefore returns `{}` and the strategies use their defaults.

## Verdict (the three video strategies)

**None of the three is enabled.** All stay as dormant modules
(`CRYPTO_{EMA_JAGUAR,BB_REVERSAL,VP_EDGE}_ENABLED` default `false`, no chip on
the Crypto tab). This matches the standing finding in
`memory/strategy-findings.md`: every intraday config tested is net-negative after
real costs; friction is the binding constraint. `retune_all()` still runs
nightly — if the walk-forward net ever turns stably positive as more history
accumulates, flip the flag.

---

# candle_renko — removed 2026-09-08

Enabled at Richard's request, then removed the same day after live paper
confirmed the backtest. 22 paper trades in ~8 hours, **1 win / 21 losses**,
−$81. The 90-day backtest had already measured **−$7,605 over 3,520 trades**
(23% win rate) and the walk-forward tuner found **no eligible combo in 24**
(best still −$4,691 OOS). Every candle_renko entry the live day was
`bearish 5m bar, 15m ST down, renko down` — it shorted every 5m red candle while
the 15m Supertrend sat bearish and spot chopped sideways, each trade a fresh
~0.1%-price coin-flip (the 10%-of-P&L stop at 100× leverage) minus fees. Not a
tuning problem — structural 5m-scalp friction, same as the three video
strategies. Module and `renko.py` deleted; replaced by `fvg_scalp`.

---

# fvg_scalp — 2026-09-08

Replaces `candle_renko` as the third crypto strategy (with `ny_n_break` and
`ichimoku`). Designed here, not from a video. Module `crypto/strategies/
fvg_scalp.py` + `crypto/strategies/fairvalue.py`.

**Setup:** a fast move leaves a 3-candle Fair Value Gap on the 5m frame; price
retraces to retest the gap zone; a candlestick pattern (engulfing / hammer /
shooting-star) confirms. Two contexts, chosen per setup:

- **continuation** — the 15m Supertrend and the 5m swing structure both agree
  with the gap direction.
- **reversal** — the trend is flat/mild and price is `stretch_atr × ATR` beyond
  the fast EMA, into an opposing gap.

**Filters:** minimum gap width (`fvg_min_atr × ATR`); a real impulse candle
(range ≥ `impulse_atr_mult × ATR` *and* volume ≥ `impulse_vol_mult × mean`); the
15m trend read; 5m market structure (HH/HL vs LH/LL); and an IST session window
(`session_start_ist`–`session_end_ist`, default 13:00–23:00, skipping the dead
Asian afternoon). **Exit:** the shared P&L trailing engine (same `TrailConfig`
as the other two lanes), a 5m close through the far side of the entry gap, or
session end.

## Backtest — defaults, 90 days, BTCUSD / ETHUSD / SOLUSD / PAXGUSD

| symbol | trades | net USD | win rate |
|---|---:|---:|---:|
| BTCUSD | 224 | **−$547** | 25% |
| ETHUSD | 218 | **−$146** | 22% |
| SOLUSD | 220 | **−$766** | 26% |
| PAXGUSD | 163 | **−$23** | 18% |
| **total** | **825** | **−$1,483** | 23% |

~9 trades/day across four symbols, `avg win $3.70` vs `avg loss −$3.47` — a
symmetric ~1:1 payoff at a 23% hit rate, EV ≈ −$1.8/trade. **5× better than
`candle_renko` (−$7,605) and far better than the video strategies** — the FVG +
impulse + structure + session filters genuinely cut the trade count (9/day vs
candle_renko's 40) and squared up the payoff — but the win rate is still too low
to clear friction. Same structural verdict as everything else on the 5m frame.

Auto-tune (`optimize_one("fvg_scalp", days=90)`): _running — add OOS result._

## Status

`CRYPTO_FVG_SCALP_ENABLED` defaults `true` — it runs in the **paper** lane
only, as the third strategy Richard asked for. **Do not arm crypto live**: on
this measurement it bleeds ~$1.8/trade. It stays paper until the walk-forward
OOS net is stably positive across ≥ 2 symbols (the standing gate).
`retune_all()` tunes it nightly via `SEARCH_SPACE["fvg_scalp"]`.

---

# ema_pivot — 2026-09-08

Fourth crypto strategy, from the CoinSwitch "EMA + Pivot" video (Ahmed Lekhan)
Richard sent — he felt `ichimoku` wasn't triggering enough. Module
`crypto/strategies/ema_pivot.py` + `crypto/strategies/pivots.py`.

**Setup:** 5-minute. Two support/resistance types must agree — **horizontal**
(standard daily pivots P / R1-R3 / S1-S3, from the prior UTC-day OHLC, held all
day) and **dynamic** (the 9/13/21 EMA fan). Enter only when the fan is stacked
*and* sloping in the trend direction (9 > 13 > 21 rising for longs), price is on
the supporting side of the day pivot P, and the last 5m close breaks a pivot
level — skipping an oversized trigger candle (> `big_candle_atr × ATR`, a
retracement risk per the video). **Exit:** the shared P&L trailing engine, a
close back through the 9 EMA, or price stretched `stretch_atr × ATR` from the
9 EMA (the video's "moved too far from the average, book it").

## Backtest — defaults, 90 days, BTC / ETH / SOL / PAXG

| symbol | trades | net USD | win rate |
|---|---:|---:|---:|
| BTCUSD | 416 | **−$1,226** | 17% |
| ETHUSD | 372 | **−$261** | 22% |
| SOLUSD | 451 | **−$1,414** | 26% |
| PAXGUSD | 415 | **−$61** | 18% |
| **total** | **1,654** | **−$2,961** | 21% |

~18 trades/day — **much more active** than ichimoku (Richard's complaint) and
2× fvg_scalp. `avg win $4.34` vs `avg loss −$3.38` (1.28:1) at a 21% hit rate,
EV ≈ −$1.8/trade. The EMA-fan + pivot-break filter fires often but the win rate
is the same 5m-friction problem — a pivot break on the 5m frame is more noise
than signal after the fee (~0.1% round-trip eats a third of the average win).

## Filter sweep + walk-forward (2026-09-09)

"Can we make it better?" — swept the video's confluence filter over 90 days,
BTC + ETH:

| variant | trades | net USD | win rate |
|---|---:|---:|---:|
| baseline | 172 | −$394 | 18% |
| `confluence_atr` 1.0 | 82 | **−$125** | **27%** |
| `min_fan_atr` 0.6 | 139 | −$354 | 16% |
| confluence 1.0 + fan 0.6 | 26 | −$47 | 23% |

The confluence rule (broken pivot must sit within 1×ATR of the slow EMA) throws
away the two-thirds of pivot breaks that fire away from the fan — it roughly
halves the bleed and lifts the hit rate ~9 points. `crypto.lanes._ema_pivot_cfg`
turns it on operationally. It does **not** turn the strategy positive.

Walk-forward `optimize_one("ema_pivot", days=45)` (BTC + ETH, 12 combos):
**0 eligible combos, best −$539.** Confirms the enable-gate is not met.

Auto-tune: `retune_all()` picks it up nightly; `SEARCH_SPACE["ema_pivot"]` now
sweeps `confluence_atr` too.

## Status

`CRYPTO_EMA_PIVOT_ENABLED` defaults `true` — runs in the **paper** lane as the
fourth strategy Richard asked for, and it does solve the "not enough trades"
problem. **Do not arm crypto live**: net-negative on every measurement, and the
walk-forward gate (stable positive OOS across ≥ 2 symbols) is not met.

The live crypto strategies are now **`ny_n_break`** (6 PM), **`ichimoku`**, and
**`ak_roxx_pro`** — all paper. **`fvg_scalp` and `ema_pivot` were removed
2026-09-10** (Richard) — both net-negative on every measurement and never cleared
the cost floor; the sections below are kept as the record of what was tried.

---

# ak_roxx_pro — 2026-09-10 (rebuilt)

The "AK Roxx" TradingView indicator (Richard's paid portal). Re-ported from the
portal's own client-side signal engine — `crypto/strategies/ak_roxx_pro.md` has
the full spec. **The old −$8k backtest (2026-09-09) was on a wrong
reconstruction (21/34/55 EMA ribbon, guessed constants) and is void.**

Real Alpha 1 entry = eight reads: `SMA(high,8)` & `SMA(low,8)` both rising,
close above the upper band and the prior close, `EMA7 > EMA14` both rising,
price beyond the previous hour's CPR, and the `EMA(hlc3, 13/21/34)` ribbon
stacked & sloping. Trend-ride exit (P&L trail or 1:2). Optional `require_alpha2_agree`
adds the portal's "Alpha 2" (15-bar break + Choppiness(14)<38.2 + Supertrend(3,10)).

Wired as a **paper** lane (`CRYPTO_AK_ROXX_ENABLED`, default on) and to the
nightly optimiser (`SEARCH_SPACE["ak_roxx_pro"]` — CPR gate on/off, Alpha 2 gate,
slope lookback, target R).

**Backtest (corrected logic), BTCUSD 45 days:** −$2,174, **844 trades**, win 29%,
avg win $3.99 / avg loss −$5.26. Edge-triggering the signal only trimmed it from
933 trades / −$2,433 — it is still ~19 trades/day.

The 8-condition entry is strict, but the **exit** is the crypto lane's shared
P&L-percent trail: 10% of P&L at 100× ≈ a **0.1% price move**. During a trend the
confluence flickers on and off, and after every tiny stop-out the strategy
re-enters — the same "each trade a 0.1% coin-flip minus fees" structural friction
RESULTS.md already recorded for the deleted 5m-scalp strategies. The portal's
Alpha 1 holds each trade to a **channel-edge** stop (far wider) and takes a
handful of signals a day, not 19. Matching that would mean giving this strategy a
price-based stop at the ``SMA(low, 8)`` / ``SMA(high, 8)`` band instead of the
shared trail — a design change, pending Richard's call.

**Do not arm crypto live.** Net-negative like every other 5m config here.
