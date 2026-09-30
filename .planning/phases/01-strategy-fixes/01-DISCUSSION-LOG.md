# Phase 1: Strategy Fixes - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-30
**Phase:** 1-Strategy Fixes
**Areas discussed:** Which lever to pull first, What counts as "fixed", How changes get proven before shipping, Scope of the confidence-ladder requirement

---

## Which lever to pull first

| Option | Description | Selected |
|--------|-------------|----------|
| Finish the OI-wall + fake-breakout work | Already partially built (Sept 12), uses the tools Richard wants leaned on | ✓ |
| Tighten the ML gate instead | Model already flagged "OI-aligned setups only win 33%" | |
| Something else | A different idea | |

**User's choice:** Finish the OI-wall + fake-breakout work.
**Notes:** Follow-up questions in this area:
- Focus first on NIFTY (worst performer, 20% win rate, -₹4,300/10 trades, most-traded) vs. all three at once → **NIFTY first**
- Fold the ML model's "OI-aligned setups only win 33%" gate-tightening insight into the same tuning pass, vs. keep separate → **Fold it in now**
- Include improving the buy-lane ML model itself (36% holdout accuracy, 43 trades) this phase, vs. rule-based filters only → **Rule-based filters only** — too little data to meaningfully improve the model yet
- Stay paper-only until proven, vs. some other arming approach → **Paper-only until proven**

---

## What counts as "fixed"

| Option | Description | Selected |
|--------|-------------|----------|
| ≥65% win rate on NIFTY buy | Matches Richard's previously stated buy-lane precision bar | ✓ |
| Just measurably better | No fixed number, iterate | |

**User's choice:** ≥65% win rate on NIFTY buy.
**Notes:** Follow-up: sample size needed before trusting that number → **40+ trades** (matches the existing confidence-ladder threshold). Follow-up: should the bar also require net-positive rupees over the same sample, not just win rate → **Yes, both** — a high win rate that still nets negative isn't actually fixed.

---

## How changes get proven before shipping

| Option | Description | Selected |
|--------|-------------|----------|
| Backtest first, then paper | Real option-chain data exists since Sept 23; cheap sanity check before paper | ✓ |
| Straight to paper | Skip backtest, let the 40-trade ladder be the only proof | |

**User's choice:** Backtest first, then paper.
**Notes:** Follow-up: the real option-chain data is only ~1 week deep right now, a backtest on it won't be conclusive — run it anyway as a sanity check now, or wait for more data? → **Run it now as a sanity check** — won't be conclusive alone, but catches an obviously broken idea early; the 40-trade paper ladder remains the real proof.

---

## Scope of the confidence-ladder requirement

| Option | Description | Selected |
|--------|-------------|----------|
| Correct, no new code | STRAT-02 is a process rule the plan must respect, not a feature to build | ✓ |
| Something needs building | A gap in ladder enforcement that this phase should also fix | |

**User's choice:** Correct, no new code.
**Notes:** None.

---

## Claude's Discretion

- Exact new/adjusted gate parameter values (within the OI-wall/fake-breakout/REQUIRE_SUPERTREND_ALIGN/CPR-gate space) — Richard specified the levers and the target, not the exact tuning values.

## Deferred Ideas

- Improving the buy-lane ML model itself — explicitly deferred, not this phase (too little training data yet).
- BankNifty/Sensex-specific tuning — implied follow-on within this phase's later plans once NIFTY proves out, not treated as its own separate phase.
