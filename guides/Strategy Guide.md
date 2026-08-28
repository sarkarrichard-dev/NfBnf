# Strategy Guide

NIFTY and BANKNIFTY index options (NSE FNO) using Dhan intraday charts and live option-chain OI.

## Apex Pivot-Trend (Theta Gainers style)

Based on [this walkthrough](https://youtu.be/bLVNs9HD1dw) — **Apex / Momentum option selling** on 5m NIFTY, SENSEX (and BANKNIFTY in the same codebase).

| Piece | Rule |
|-------|------|
| Pivots | Standard PP/R1/S1 from **previous day** HLC (not Camarilla) |
| Supertrend | **(7, 3)** on 5m spot (tighter than default 10,3) |
| Long / sell put | 5m **close > R1** and ST **bullish** → `SELL_ATM_PUT` |
| Short / sell call | 5m **close < S1** and ST **bearish** → `SELL_ATM_CALL` |
| Sideways | Price **between S1 and R1** → **no entry** |
| Exit | Supertrend flip or **15:15** square-off |
| Limits | Max **3** trades per index per day; **no entry after 15:00** IST |

Enable:

```env
STRATEGY_STYLE=APEX
SENSEX_SECURITY_ID=51
APEX_USE_HEDGED_SPREADS=true
```

`APEX_USE_HEDGED_SPREADS=true` (default) maps ATM sells to **bull put / bear call spreads** for defined risk. Set `false` only if you accept **naked** short ATM options like the video (unlimited risk on the short leg).

## CPR + EMA (spot 5m)

- CPR from the previous session (pivot, BC, TC).
- Default EMAs: **8** (fast) and **20** (slow) on index spot — set `EMA_FAST_PERIOD` / `EMA_SLOW_PERIOD` in `.env`.
- **Long premium:** price above TC and fast EMA > slow → `BUY_CALL`; below BC and fast < slow → `BUY_PUT` (plus Supertrend when enabled).

### AUTO autopilot (default)

When `STRATEGY_STYLE=AUTO`, `AUTO_INTELLIGENT_ROUTING=true`, and `AUTO_INCLUDE_APEX=true` (all default), **each scan** picks the best subsystem:

| Priority | Market read | Entry | `strategy_mode` |
|----------|-------------|--------|-----------------|
| 1 | **Above R1** or **below S1** + Apex Supertrend (7,3) aligned | Bull put / bear call (hedged if `APEX_USE_HEDGED_SPREADS=true`) | `apex` / `apex_hedged` |
| 2 | Fresh **8/20 cross** (no CPR conflict) | Directional credit spread | `ema_cross` |
| 3 | **Sideways CPR** + flat EMA | Iron condor | `cpr_sideways` |
| 4 | **Trending CPR** + EMA aligned | Bull put / bear call | `cpr_trend` |
| 5 | Conflicts / no setup | Buy rules or wait | `conflict` / `wait` / `apex_wait` |

Apex session limits (09:16–15:00 entries, max 3 trades/index/day) apply to Apex picks inside AUTO as well.  
Force Apex-only: `STRATEGY_STYLE=APEX`. Disable Apex inside AUTO: `AUTO_INCLUDE_APEX=false`.

**SENSEX** runs with the other indices when `SENSEX_SECURITY_ID=51` (Dhan BSE INDEX / IDX_I).

Exits also switch automatically: EMA flip closes directional credit; range end or EMA cross closes iron condor; CPR regime flip closes `cpr_trend` positions.

Fixed cross-only mode: `AUTO_INTELLIGENT_ROUTING=false` + `REQUIRE_EMA_CROSS_FOR_CREDIT=true`.  
Legacy CPR-only credit: `AUTO_INTELLIGENT_ROUTING=false` + `REQUIRE_EMA_CROSS_FOR_CREDIT=false`.

## CPR regime (sideways vs trending)

Before each scan the app measures **CPR width** = (TC − BC) as % of pivot:

| Width | Typical day | `day_bias` | Hedged credit structure (`STRATEGY_STYLE=AUTO`) |
|-------|-------------|------------|--------------------------------------------------|
| **Wide** (≥ ~0.75%) | Range / sideways | `SIDEWAYS` | **Iron condor** — sell OTM call + put, buy wings |
| **Narrow** (≤ ~0.35%) | Breakout / trending | `TRENDING_BULL` / `TRENDING_BEAR` | **Bull put spread** or **bear call spread** |
| Normal | Mixed | `MIXED` | Falls back to buy premium + Supertrend filters |

Also tracked:

- **Virgin CPR** — today has not traded through yesterday’s BC–TC zone.
- **CPR type** — prior close above high (bullish), below low (bearish), or inside range.
- **Price position** — above / below / inside CPR.

Set in `.env` (see `.env.example`; **restart the server** after edits). The dashboard **Strategy tuning** panel shows active values.

| Variable | Default | Purpose |
|----------|---------|---------|
| `STRATEGY_STYLE` | `AUTO` | `AUTO` / `CREDIT` / `BUY` |
| `EMA_FAST_PERIOD` | `8` | Fast EMA on spot |
| `EMA_SLOW_PERIOD` | `20` | Slow EMA on spot |
| `AUTO_INTELLIGENT_ROUTING` | `true` | AUTO picks cross / range / trend credit per scan |
| `REQUIRE_EMA_CROSS_FOR_CREDIT` | `true` | Used when intelligent routing is off |
| `EXIT_CREDIT_ON_EMA_CROSS_FLIP` | `true` | Exit credit when EMA flips against position |
| `CREDIT_IRON_CONDOR_WITHOUT_CROSS` | `false` | Sideways iron condor without a cross |
| `ENABLE_CREDIT_STRATEGIES` | `true` | Allow credit structures in AUTO |
| `CPR_NARROW_WIDTH_PCT` | `0.35` | At or below → trending bias |
| `CPR_WIDE_WIDTH_PCT` | `0.75` | At or above → sideways bias |
| `CREDIT_SHORT_STRIKE_STEPS` | `2` | Short leg distance from ATM (× strike step) |
| `CREDIT_WING_STRIKES` | `2` | Hedge wing width (× strike step) |
| `CPR_CREDIT_MIN_CONFIDENCE` | `0.58` | Base confidence for credit entries |
| `REQUIRE_SUPERTREND_ALIGN` | `true` | Block buys against Supertrend |
| `REQUIRE_BREAKOUT_TAG` | `false` | Require Break Res/Sup for buys |

**Presets** (copy into `.env`):

- More sideways credit: `CPR_WIDE_WIDTH_PCT=0.65`, `CPR_NARROW_WIDTH_PCT=0.30`
- Stricter trending buys: `REQUIRE_BREAKOUT_TAG=true`, `CPR_NARROW_WIDTH_PCT=0.40`
- Buy only: `STRATEGY_STYLE=BUY`

## Supertrend + Break Res / Break Sup (AK Roxx / CPR by AAK style)

After CPR+EMA aligns, the scanner applies:

| Filter | Long (`BUY_CALL`) | Short (`BUY_PUT`) |
|--------|-------------------|-------------------|
| **Supertrend** (10, 3) | Must be bullish (+1) | Must be bearish (−1) |
| **Break Res** | Close above prior 20-bar high → +confidence | Blocks short if fired |
| **Break Sup** | Blocks long if fired | Close below prior 20-bar low → +confidence |

- **Break Res / Break Sup** — rolling range break on 5m closes (tags like your TradingView labels).
- **Supertrend** — ATR bands; mismatched trend blocks the trade.
- **Exit** — open trades also exit on Supertrend flip or price through the live Supertrend stop (refreshed each trail cycle).

Tunables in `index_ai/strategies/strategy_params.py` (`require_breakout_tag` for stricter Roxx-style entries only on Break Res/Sup).

## Open interest (Dhan option chain)

On each scan the app loads the nearest expiry chain and reads:

- **PCR** — put OI / call OI near ATM (±5 strikes).
- **Bias** — call-heavy, put-heavy, or balanced.
- **Strike pick** — ATM ±2 strikes with best OI + volume (not only nearest strike).

OI adjusts confidence: +6% when OI agrees with direction, −8% when it fights it. Trades below the confidence gate are skipped.

## ML learning (scikit-learn)

After **8+ closed trades**, the app trains a **logistic regression** model on journal features (confidence, EMA spread, CPR distance, PCR, index, time of day). The model is saved under `memory/models/` and **versioned on each retrain**.

- **Auto-retrain** when a trade closes or you open the Learning panel.
- **ML gate** — new setups below the learned min win-probability (default ~45%) are blocked (scanner and manual execute).
- **Retrain ML** button forces a fresh train from all closed trades.
- Requires `scikit-learn` (`pip install scikit-learn`).

Rule-based feedback learning (min confidence ±%) still runs alongside the ML gate.

## Hugging Face (FinBERT)

Set `HF_TOKEN` in `.env` (free token at huggingface.co). The app uses **ProsusAI/finbert** via the Inference API to score each setup narrative (bullish/bearish language from CPR, EMA, OI context).

- Blocks entries when FinBERT is strongly **negative** or below `HF_MIN_POSITIVE_PROB` (default 42%).
- Exports closed trades to `memory/hf/outcomes.jsonl` for datasets / future fine-tuning.
- **Sync HF dataset** / **Upload to Hub** on the Learning panel (`HF_DATASET_REPO=you/index-options-outcomes`).
- Optional offline mode: `HF_USE_LOCAL=true` and `pip install -e ".[hf-local]"`.

## Credit spread exits (hedged selling)

When `STRATEGY_STYLE=AUTO` or `CREDIT` fires an iron condor / bull put / bear call spread:

| Exit | Default | Meaning |
|------|---------|---------|
| `ENABLE_PROFIT_TRAIL` | true | No fixed profit cap — trail peak MTM profit and exit on giveback. |
| `PROFIT_TRAIL_ARM_RUPEES_PER_LOT` | 500 | Start trailing after this much profit per lot (× dashboard lots). |
| `PROFIT_TRAIL_GIVEBACK_PCT` | 0.25 | Exit when profit falls 25% from the session peak (e.g. peak ₹1000 → floor ₹750). |
| `CREDIT_PROFIT_TARGET_PCT` | 0.50 | Used only if `ENABLE_PROFIT_TRAIL=false`. |
| `CREDIT_STOP_LOSS_PCT` | 0.60 | Close when loss reaches 60% of defined max loss (wing width − credit). |
| Short-strike breach | — | Bull put: index below short put; bear call: above short call; iron condor: beyond either short. |

MTM marks **all legs** (net debit to close vs entry credit). Dashboard shows each leg, net credit, and max loss.

Directional **index-point trails** apply only to long premium (`BUY_CALL` / `BUY_PUT`), not credit spreads.

## Trailing stop (two phases) — long premium only

Exits were too early with a 1-point index trail. New logic per index:

| Index | Initial stop | Arm trail after | Trail distance |
|-------|----------------|-----------------|----------------|
| NIFTY | 100 pts | +25 pts profit | 40 pts behind peak |
| BANKNIFTY | 200 pts | +50 pts profit | 80 pts behind peak |

1. **Before activation** — only a wide initial stop (noise does not exit the trade).
2. **After activation** — trail behind the best favorable index move.

Square-off still closes open positions in the last minutes of the session (IST).

## Execution safety (finance-grade gates)

Every entry and live exit passes centralized checks in `index_ai/execution_safety.py`:

- **Strategy**: blocks `conflict` / `wait` AUTO modes and EMA-vs-credit mismatches (e.g. bear call while EMA bullish).
- **Structure**: action must match option legs (iron condor = 4 legs, spreads = 2, single buy = 1).
- **Quantity**: each leg matches NSE lot × dashboard lots (1–10).
- **Duplicates**: no second open position on the same index + mode.
- **Live**: requires `ALLOW_LIVE_TRADING`, Dhan token, kill switch clear; exits only on **LIVE_TRADED** with broker fill proof.
- **Lock**: per-index mutex so two scans cannot double-post orders.

The scanner uses the **plan** from `plan_instrument` (not a bypass). Live orders are validated again immediately before each Dhan API call.

## Risk (hard-coded)

- Lots per trade: use **Execution mode → Lots per trade** (+/−) on the dashboard (1–10 NSE lots). Saved in `memory/`; applies to new scanner trades and re-syncs open journal quantities. Optional default: `LOTS_PER_TRADE` in `.env`.
- **Risk scales with lots**: daily loss cap = **₹6,000 × lots** (2 lots → ₹12,000). Credit spread profit/stop targets scale with order quantity automatically.
- Kill switch (**Live only**): **3 consecutive** losing closed trades in one IST day, or daily realized loss hits the scaled cap. A winning trade resets the loss streak. Paper mode is not blocked.
- ₹6,000 daily loss cap
- Paper or Live via dashboard toggle
