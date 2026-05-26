# Strategy Guide

NIFTY and BANKNIFTY index options (NSE FNO) using Dhan intraday charts and live option-chain OI.

## CPR + EMA

- CPR from the previous session (pivot, BC, TC).
- Bullish: price above TC and EMA 9 > EMA 21 → `BUY_CALL`.
- Bearish: price below BC and EMA 9 < EMA 21 → `BUY_PUT`.

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
- 3 losing trades / day kill switch
- ₹6,000 daily loss cap
- Paper or Live via dashboard toggle
