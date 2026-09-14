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

## ak_roxx_pro — 2026-09-11 (faithful port, exit verified)

Read `indicators.js::computeAlpha1Signals` line-for-line off the portal (the
content filter was worked around). **The entry was right all along.** The
**exit** was wrong in every earlier port. The real one, verbatim:

    if (buyActive && c.close < loC) buyActive = false;   // loC = current SMA(low, 8)

No target, no P&L trail, no ratchet, no hard floor. A long rides until the first
bar that **closes below the current 8-period average of the lows**. The drawn
T1/T2/SL lines are a separate display annotation — they don't close the trade.

`ak_roxx_pro.py` rewritten to match exactly. `SEARCH_SPACE["ak_roxx_pro"]` now
sweeps `require_beyond_cpr`, `require_alpha2_agree`, `upper_len`, `lower_len`.

**Backtest of the faithful version, BTC + ETH:**

| timeframe | trades | net | win | avg win / loss |
|---|---|---|---|---|
| 5m, 45d | 1,255 | −$2,337 | 16% | $6.46 / −$3.48 |
| 15m, 45d | 426 | ≈ flat | 20% | — |
| **1h, 90d** | **192** | **−$376** | **27%** | **$21.83 / −$10.79** |

**The timeframe was the other bug.** On 5m the `close < SMA(low,8)` exit is a
40-minute leash — a routine pullback closes below it, so the trend-ride gets
chopped out (28 trades/day, 16% win). On **1h** the same exit is 8 hours of
lows, a real swing stop: ~2 trades/day, and the winners actually run — avg win
$21.83 vs avg loss $10.79, a **2:1 payoff**. Still net-negative (−$376 / 90d ≈
−$4/day, basically flat with a small bleed) and ~6 points of win-rate short of
breakeven — but it is the **closest any crypto strategy has come**, and it's the
timeframe the channel-break exit is actually built for.

Lane default set to **1h** (`AkRoxxConfig.timeframe`, env `CRYPTO_AK_ROXX_TF`).
The optimiser sweeps `upper_len` / `lower_len` / the CPR gate — that might close
the gap, or not. Worth the forward paper week. **Do not arm crypto live.**

---

# funding_squeeze — 2026-09-15 (Claude-proposed, not from a video)

Every crypto strategy above is trend-following (follow the move once it's
already happening). All are flat or losing. This one is the opposite kind of
bet: perps pay a **funding rate** every settlement to keep the perp price tied
to spot; when a coin's funding is unusually extreme *relative to its own
recent history*, one side is unusually crowded and leveraged, and a stall is
often enough to force that crowd to unwind. Fades the crowded side (short when
funding is far above its own rolling average and the bar just turned red, long
when far below and the bar just turned green) once a rolling z-score of
funding crosses `z_threshold`. Module `crypto/strategies/funding_squeeze.py`.

**Data note:** Delta's published API docs have no funding-rate-history
endpoint. `crypto/delta/market_data.funding_rate_history()` uses an
undocumented `FUNDING:<symbol>` pseudo-symbol on the same public
`/v2/history/candles` route (found by probing the live API 2026-09-15) — it
returned ~200 days of real hourly history for BTCUSD when checked. Verify it
still works before trusting a result built on it; Delta could change or
remove it without notice since it isn't a supported endpoint.

## Backtest — real Delta history + real charges, 150 days, all 6 live symbols

| symbol | trades | net USD | win rate |
|---|---:|---:|---:|
| BNBUSD | 23 | **−$24** | 30% |
| BTCUSD | 17 | **−$58** | 29% |
| ETHUSD | 20 | **−$57** | 20% |
| SOLUSD | 5 | **−$38** | 20% |
| XRPUSD | 5 | **+$39** | 40% |
| PAXGUSD | 0 | — | never crosses the threshold (gold-backed perp, funding barely moves) |
| **total** | **70** | **−$138** | 27% |

`avg win $27.08` vs `avg loss −$12.80` — a genuinely good ~2:1 payoff, unlike
every trend-following lane above, but the 27% hit rate isn't enough to clear
it (EV ≈ −$2/trade). A `z_threshold` sweep (2.0 / 2.5 / 3.0) stayed
net-negative at every setting with no consistent direction (2.5 was the least
bad, 2.0 and 3.0 both worse) — the pattern strategy-findings.md already warns
about: a lone better-looking setting with neighbours that flip sign is noise,
not a real optimum.

## Status

**Not wired into `crypto/lanes.py`.** Net-negative on the only measurement run
so far, same structural verdict as everything else on this platform — the
payoff shape is more interesting than any existing crypto lane's, so it isn't
being written off outright the way `ema_pivot`/`fvg_scalp` were, but it has
not earned a paper slot either. Kept as research tooling
(`crypto/strategies/funding_squeeze.py`, `crypto.delta.market_data.
funding_rate_history`, and the `funding_squeeze` entry in `crypto/backtest.py`)
in case a different confirmation filter (the current one is just "the last
candle turned the opposite colour") is worth trying later.
