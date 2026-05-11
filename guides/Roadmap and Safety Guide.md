# Roadmap And Safety Guide

The workstation can learn from market data, analyze symbols, create paper-trade plans, and record paper orders.

Live broker orders are still blocked. That is deliberate.

## What Works Now

- Download 6-7 years of Indian daily market data.
- Build supervised training rows.
- Train the local market brain.
- Run symbol analysis.
- Create risk-sized paper-trade plans.
- Store paper orders and evolution events locally.
- Export learning rows for Hugging Face experiments.

## Before Live Trading

1. Prove the data is clean.
2. Backtest with realistic costs.
3. Paper trade for at least 20 market sessions.
4. Add a broker order router.
5. Test a manual kill switch.
6. Enforce max daily loss, max position size, and max trade count.
7. Keep every order traceable to a model version and reason.
