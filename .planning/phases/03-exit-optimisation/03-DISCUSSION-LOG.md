# Phase 3: Exit Optimisation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-03
**Phase:** 3-Exit Optimisation
**Areas discussed:** Which markets get re-checked, What happens when a stop looks wrong, Deleting the old trail safely, What counts as drifting and how Richard hears about it, How the re-check runs again later

---

## Which markets get re-checked

| Option | Description | Selected |
|--------|-------------|----------|
| India options only | NIFTY/BANKNIFTY/SENSEX buy and sell, matches roadmap | |
| India + crypto | Also crypto's 1.6% trail | |
| Everything | India, crypto and commodities | ✓ |

**User's choice:** Everything.
**Notes:** Widens the roadmap's India-only wording; thin-data segments handled by the 40-trade gate.

---

## What happens when a stop looks wrong

| Option | Description | Selected |
|--------|-------------|----------|
| Suggest a better number, Richard approves | Dashboard suggestion in rupees/win-rate, nothing changes until approved | ✓ |
| Only tell me it looks off | Warning only, no suggested value | |

**User's choice:** Suggest a better number, human approves.
**Notes:** Minimum data before it may speak: 40 trades and 14 days (chosen over 20 trades).

---

## Deleting the old trail safely

| Option | Description | Selected |
|--------|-------------|----------|
| Delete fully, move history into notes | Remove code + tests, preserve lessons in strategy-findings | ✓ |
| Delete code, skip the history | Rely on git | |

**User's choice:** Delete fully and preserve history.
**Notes:** Trailing-profit rule: Richard confirmed the point-based trail (stop follows price 1-for-1) counts as both trailing stop and trailing profit; no second mechanism. Alternative (keep a separate profit trail) not chosen.

---

## What counts as drifting and how Richard hears about it

| Option | Description | Selected |
|--------|-------------|----------|
| >15 points from last check, once 40+ trades | Plain, avoids false alarms | ✓ |
| >10 points | More sensitive | |
| Claude decides | | |

**User's choice:** 15 percentage points, 40+ trades.
**Notes:** Alert: Telegram message plus dashboard flag (chosen over dashboard-only).

---

## How the re-check runs again later

| Option | Description | Selected |
|--------|-------------|----------|
| Automatic daily after close, plus a button | | ✓ |
| Only when button pressed | | |

**User's choice:** Daily automatic run plus a dashboard button.
**Notes:** Crypto has no close — exact run time left to Claude.

---

## Claude's Discretion

- End-of-day run time; where baseline numbers are stored (must survive restart).
- Dashboard flag/button wording and placement.
- Where exit reasons actually live in the journal (initial scouting did not find them under the obvious keys).

## Deferred Ideas

- Auto-applying suggested stop distances — ruled out.
- Removing the rupee profit_trail fallback — not this phase.
