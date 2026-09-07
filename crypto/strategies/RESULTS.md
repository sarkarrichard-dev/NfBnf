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

# candle_renko — 2026-09-08

Richard's own spec: 5-minute candlestick reversal bar (engulfing / hammer /
shooting-star) gated by a 15-minute Supertrend trend and an ATR-sized Renko
brick-direction agreement; exit on the P&L trailing engine or a 15m Supertrend
flip. Module `crypto/strategies/candle_renko.py`.

## Backtest — video-style defaults (atr_len 14, renko_atr_mult 1.0, st 10/3.0), 90 days

| symbol | trades | net USD | win rate |
|---|---:|---:|---:|
| BTCUSD | 1233 | **−$2,026** | 22% |
| ETHUSD | 1217 | **−$570** | 22% |
| SOLUSD | 1188 | **−$2,619** | 24% |
| **total** | **3638** | **−$5,215** | 22% |

~40 trades/day, `avg win $3.24` vs `avg loss −$2.77` — the same ~1:1 payoff at a
~22% hit rate that sank the three video strategies, and the triple filter
(pattern + Supertrend + Renko) barely thins the trade count. Structurally the
worst of the four candidates on raw net.

## Status

**Enabled anyway, at Richard's explicit request (2026-09-08): "make the renko
supertrand not dormant but an active strategy."** `CRYPTO_CANDLE_RENKO_ENABLED`
defaults `true`; it runs in the paper lane alongside `ny_n_break` and
`ichimoku`. Live orders still need `CRYPTO_TRADING_MODE=LIVE` +
`CRYPTO_ALLOW_LIVE=true` + the arm phrase — **do not arm it live**: on this
measurement it loses about as fast as fees can take it. `retune_all()` tunes it
nightly through `crypto/ml/optimize.py` (`SEARCH_SPACE["candle_renko"]`); the
walk-forward OOS result is the number to watch before it goes near live.

The live crypto strategies are now **`ny_n_break`** (6 PM), **`ichimoku`**, and
**`candle_renko`** (paper only until it clears the cost floor).
