# Phase 2: Order Placing & Tracking - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-30
**Phase:** 2-order-placing-tracking
**Areas discussed:** Which broker connections are in scope, How to test these without real money, Order of the four fixes, What counts as "fixed", What to do when the state is genuinely unclear, Crypto's always-on nature, Getting notified when this happens, Where to document the tick-feed fallback

---

## Which broker connections are in scope

| Option | Description | Selected |
|--------|-------------|----------|
| Dhan only — India + commodities | Matches the codebase notes' original file references (executor.py, dhan_orders.py, dhan.py) | |
| Dhan and Delta (crypto) both | Wider scope, two different broker APIs, same phase | ✓ |

**User's choice:** Dhan and Delta both.
**Notes:** Follow-up — does crypto going live sooner change priority? → **Crypto/Delta fixed first**, ahead of Dhan, because it's the one closest to arming real money (~November 2026).

---

## How to test these without real money

| Option | Description | Selected |
|--------|-------------|----------|
| Fake the broker connection in tests | Stand-in client, told to drop/delay/reorder — no real API calls | |
| Replay real recorded broker responses with injected faults | Real logged traffic, deliberate glitches mixed in — closer to real conditions | ✓ |
| You decide | No single rule across all four | |

**User's choice:** Replay real recorded broker responses with injected faults.
**Notes:** Follow-up — does such a recording already exist? → **Not sure, check what exists** (left to research rather than assumed either way).

---

## Order of the four fixes

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, urgent two first | Cancel-races + disconnect-reconciliation before websocket-reconnect + tick-fallback, matching CONCERNS.md's own High/Medium split | ✓ |
| No, do all four together | One combined pass, no staging | |

**User's choice:** Urgent two first.
**Notes:** None.

---

## What counts as "fixed"

| Option | Description | Selected |
|--------|-------------|----------|
| A test that simulates the exact failure and passes | Repeatable, no live risk | ✓ |
| Same, plus a live paper-trading soak period | Slower, proven under real conditions too | |

**User's choice:** A test that simulates the exact failure and passes.
**Notes:** None.

---

## What to do when the state is genuinely unclear

| Option | Description | Selected |
|--------|-------------|----------|
| Assume it didn't fill — never risk a duplicate | Safe default, could miss a real fill from the journal | |
| Ask Dhan/Delta's own order-status API to break the tie | More accurate, adds a dependency on that API being reliable too | ✓ |

**User's choice:** Ask the broker's own order-status API to break the tie.
**Notes:** Claude's discretion — if that tie-breaker API is also unreachable, fall back to "assume not filled" (the option not picked directly, but the natural fallback-of-the-fallback). Recorded explicitly in CONTEXT.md as D-07 so it isn't silently invented later.

---

## Crypto's always-on nature

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — a max time an unclear position can sit before forcing a check | Prevents a stuck position sitting silently for hours | ✓ |
| No — same handling as India, no crypto-specific timer | Trust the same reconciliation logic | |

**User's choice:** Yes, a max-unclear-time safeguard for crypto specifically.
**Notes:** Exact timeout value left to Claude's discretion.

---

## Getting notified when this happens

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, notify | Matches existing trade open/close Telegram notifications | ✓ |
| No, log it only | No real-time message | |

**User's choice:** Yes, notify via Telegram.
**Notes:** None.

---

## Where to document the tick-feed fallback

| Option | Description | Selected |
|--------|-------------|----------|
| Written guide only this phase | Avoid duplicating Phase 4's planned "Data Health" dashboard view | |
| Written guide plus a simple dashboard indicator now | Guide + a small live/fallback indicator ahead of Phase 4's fuller view | ✓ |

**User's choice:** Written guide plus a simple dashboard indicator now.
**Notes:** The fuller Data Health view stays Phase 4's job — deferred, not duplicated.

---

## Claude's Discretion

- Exact mechanism for capturing/replaying real broker traffic — left to research to determine what, if anything, already exists for Dhan and/or Delta.
- Exact crypto stuck-position timeout value.
- Exact wording/placement of the tick-status dashboard indicator — reuse existing dashboard patterns.
- Fallback-of-the-fallback if the order-status tie-breaker API is itself unreachable: assume not filled.

## Deferred Ideas

- The fuller "Data Health" dashboard view (tick age, option-chain snapshot age, measured spread age, full websocket status) — stays Phase 4's job (UIUX-02), not duplicated here.
