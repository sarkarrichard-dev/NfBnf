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

The live crypto strategies are now **`ny_n_break`** (6 PM), **`ichimoku`**, and
**`fvg_scalp`** (paper only until it clears the cost floor).
