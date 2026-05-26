# Strategy Guide

NIFTY and BANKNIFTY index options (NSE FNO) using Dhan intraday charts and live option-chain OI.

## CPR + EMA

- CPR from the previous session (pivot, BC, TC).
- Bullish: price above TC and EMA 9 > EMA 21 → `BUY_CALL`.
- Bearish: price below BC and EMA 9 < EMA 21 → `BUY_PUT`.

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

Set in `.env`:

- `STRATEGY_STYLE=AUTO` — credit when regime is clear, else directional buys (default).
- `STRATEGY_STYLE=CREDIT` — only hedged selling.
- `STRATEGY_STYLE=BUY` — only long premium (calls/puts).

Wing width and short strike distance: `index_ai/strategy_params.py` (`credit_wing_strikes`, `credit_short_strike_steps`).

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

Tunables in `index_ai/strategy_params.py` (`require_breakout_tag` for stricter Roxx-style entries only on Break Res/Sup).

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

## Trailing stop (two phases)

Exits were too early with a 1-point index trail. New logic per index:

| Index | Initial stop | Arm trail after | Trail distance |
|-------|----------------|-----------------|----------------|
| NIFTY | 100 pts | +25 pts profit | 40 pts behind peak |
| BANKNIFTY | 200 pts | +50 pts profit | 80 pts behind peak |

1. **Before activation** — only a wide initial stop (noise does not exit the trade).
2. **After activation** — trail behind the best favorable index move.

Square-off still closes open positions in the last minutes of the session (IST).

## Risk (hard-coded)

- 1 lot per trade
- 3 losing trades / day kill switch (**Live mode only** — paper keeps trading)
- ₹6,000 daily loss cap
- Paper or Live via dashboard toggle
