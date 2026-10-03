# Roadmap: QuantHawk — Product Quality + Subscription Milestone

## Overview

This roadmap has two halves. The first four phases make the existing,
single-operator product itself solid — strategy quality, order handling,
exit logic, and dashboard UX — grounded directly in this codebase's own
documented concerns (`.planning/codebase/CONCERNS.md`) and prior strategy
findings. The remaining six phases (originally the whole of this roadmap,
renumbered here) take QuantHawk from a single-operator app to a platform
that can safely hold a first paying outside subscriber's money: extracting
the module-global state that today assumes one user into genuinely isolated
per-subscriber risk, sizing, and kill-switch state (Phase 5) — the
dependency root the later subscription phases sit on — then broker
connection and credential storage (Phase 6), the existing paper/live safety
locks scoped per subscriber (Phase 7), subscriber-facing reporting and
notifications (Phase 8), subscription billing (Phase 9), and finally the
legal go-live gate (Phase 10).

Richard chose to put product-quality work ahead of the subscription
build-out: get the thing itself right before building the machinery to sell
it to strangers. If that ordering should change — e.g. running some of
Phases 1-4 in parallel with Phase 5 onward rather than strictly before it —
say so and this gets reordered.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Strategy Fixes** - Raise the option buy lane's real win rate, keep every parameter change on the confidence ladder, re-validate Strategy Lab verdicts against real option-chain data (completed 2026-09-30)
- [x] **Phase 2: Order Placing & Tracking** - Close the untested money-path edge cases: cancel-while-placing races, broker-disconnect reconciliation, websocket reconnection, tick-feed fallback (completed 2026-10-01)
- [ ] **Phase 3: Exit Optimisation** - Re-validate trailing-stop tuning against live data, remove the dead percent-of-premium code path, add drift monitoring
- [ ] **Phase 4: Dashboard UI/UX** - Surface which crypto pairs are actually live, add a Data Health view, hold every change to the existing visual bar
- [ ] **Phase 5: Tenant Isolation Foundation** - Per-subscriber risk state, sizing, kill switch, and pause control, fully separated from every other subscriber's
- [ ] **Phase 6: Broker Connection & Credential Vault** - Subscriber connects and verifies their own broker account; credentials encrypted and scoped to them alone
- [ ] **Phase 7: Per-Subscriber Trading Safety** - Paper-by-default and two-lock live arming, scoped to each subscriber's own account
- [ ] **Phase 8: Subscriber Reporting & Notifications** - Subscriber sees their own P&L, per-(strategy, instrument) breakdown, and trade/risk notifications
- [ ] **Phase 9: Subscription Billing** - Subscribe, cancel, and failed-payment handling for one flat recurring plan
- [ ] **Phase 10: Compliance & Go-Live Gate** - Static-IP egress and white-box strategy disclosure verified before real-money go-live

## Phase Details

### Phase 1: Strategy Fixes

**Goal**: The option buy lane's real, live-journal win rate moves toward Richard's ≥65% precision bar, and every strategy-parameter change stays on the existing confidence ladder rather than being shortcut.
**Depends on**: Nothing (first phase)
**Requirements**: STRAT-01, STRAT-02, STRAT-03
**Success Criteria** (what must be TRUE):

  1. The buy lane's measured live win rate has moved up from its current baseline (see `memory/strategy-findings.md` for the starting numbers) — reported in rupees and win-rate points, not abstract terms
  2. No strategy-parameter change ships without going through the 15-trade observe-only / 40-trade human-approved-suggestion ladder, even under time pressure
  3. Every Strategy Lab verdict dated before 2026-09-23 is either re-validated against real option-chain data or explicitly flagged as pre-cutover and untrustworthy for absolute numbers

**Plans**: 4/4 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Tracer: CPR-direction buy gate (off by default) + live buy lane replayed on the real recorded chain, NIFTY first (wave 1)
- [x] 01-02-PLAN.md — Retire the Black-Scholes-proxy buy-lane viability numbers; scorecard `since` cut-off to judge tuned entries on their own trades (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-03-PLAN.md — OI-wall room gate + 3-bar breakout confirmation in the tuned bundle, dashboard switch rows, three-index real-chain sanity check (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-04-PLAN.md — Richard's go/hold decision, .env switch-on for paper, baseline and measuring recipe recorded (wave 3, checkpoints)

### Phase 2: Order Placing & Tracking

**Goal**: The order-placement and position-tracking code survives the specific failure modes already flagged as untested — a cancel racing a placement, a broker disconnect mid-position, a websocket drop under load.
**Depends on**: Nothing
**Requirements**: ORD-01, ORD-02, ORD-03, ORD-04
**Success Criteria** (what must be TRUE):

  1. A test proves a cancel issued while an order is still being placed produces neither a duplicate order nor an orphaned one
  2. A test proves a position is correctly reconciled (no stale or duplicate entry) after a simulated broker disconnection
  3. The Dhan websocket reconnecting mid-scan-cycle does not silently drop tick data a stop-trigger depends on — verified under a simulated busy cycle (20+ concurrent candles)
  4. When the tick feed lags or drops, stop triggering visibly falls back to candle-based evaluation, and this behavior is documented, not just known by whoever wrote it

**Plans**: 7/7 plans executed

Plans:
**Wave 1**

- [x] 02-01-PLAN.md — Tracer (crypto first): lost Delta entry reply settled from Delta's own order list; broker-traffic recorder + shared replay helper; unconfirmed-entry hold (ORD-01, ORD-02)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 02-02-PLAN.md — Crypto: Close-all during placement, Delta outage handling, 180 s stuck-position alert, reconcile retry (ORD-01, ORD-02)
- [x] 02-03-PLAN.md — Dhan: no re-sent order after a lost reply (entries and exits), cancel_order, every accepted order journalled (ORD-01, ORD-02)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 02-04-PLAN.md — Dhan: Close / Close-all / stop on a still-pending entry cancels it under the placement lock (ORD-01)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 02-05-PLAN.md — Dhan: disconnect-safe live sync and reconciliation, Telegram alert on every reconcile problem (ORD-02)

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 02-06-PLAN.md — Dhan websocket: no received tick lost on a drop, prompt reconnect, busy-cycle proof on recorded frames (ORD-03)

**Wave 6** *(blocked on Wave 5 completion)*

- [x] 02-07-PLAN.md — 20-second stop fallback proven, written into the Strategy Guide, and shown as a Ticks pill on the dashboard (ORD-04)

### Phase 3: Exit Optimisation

**Goal**: Trailing-stop tuning is kept current against live data instead of frozen at a one-time 2026-09-24/28 tune, and the codebase has exactly one trailing-stop implementation, not two contradictory ones.
**Depends on**: Nothing
**Requirements**: EXIT-01, EXIT-02, EXIT-03
**Success Criteria** (what must be TRUE):

  1. Current trailing-stop point values for every index and both lanes have been re-checked against recent live-journal data, with the comparison shown in rupees/win-rate, and a repeatable way to re-check them again later
  2. `premium_trail.py`'s percent-of-premium code path no longer exists in the codebase, and nothing still imports it
  3. A monitoring signal fires if trail-stop hit rate or win rate drifts meaningfully from its last validated baseline, so a bad tune surfaces immediately rather than weeks later in the scorecard

**Plans**: 6/6 plans executed

Plans:
**Wave 1**

- [x] 03-01-PLAN.md — Tracer: "Re-check now" end to end for the six India segments — exit-reason classifier, stats under today's stop distance, confidence-ladder verdict, stored row, GET + POST /api/exit-recheck; read-only real-journal proof (EXIT-01, EXIT-03)
- [x] 03-02-PLAN.md — Delete premium_trail.py: preserve its history first, pin and re-key the sell-lane exit suppression on SELL_TRAIL_POINTS (the trap), remove the dead branch, keep the profit_trail fallback (EXIT-02)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 03-03-PLAN.md — One trail in the docs: code comments, Strategy Guide (real 25/55/80 and 40/100/130 rule), CLAUDE.md, codebase map (EXIT-02)
- [x] 03-04-PLAN.md — India replay on recorded ticks + chain quotes, real-data feasibility run, suggest-only gate (ladder -> frozen -> replay quality -> net rupees) (EXIT-01)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 03-05-PLAN.md — Crypto + commodities segments, baselines and the 15-point drift rule, one Telegram message per drift event, daily run after the close (EXIT-01, EXIT-03)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 03-06-PLAN.md — Strategy P&L "Stop check" panel with Re-check now and the results-changed flag; last dashboard wording; concerns map (EXIT-01, EXIT-02, EXIT-03)

### Phase 4: Dashboard UI/UX

**Goal**: The dashboard shows what's actually happening (which crypto pairs are really live, whether market data is flowing) without anyone having to read server logs, and stays on the existing visual bar while doing it.
**Depends on**: Nothing
**Requirements**: UIUX-01, UIUX-02, UIUX-03
**Success Criteria** (what must be TRUE):

  1. After arming crypto live, the dashboard shows exactly which (strategy, coin) pairs are live vs. still paper — no log-reading required
  2. A Data Health view shows tick age, option-chain snapshot age, measured spread age, and Dhan websocket status, so a missed trade or a stop that didn't fire can be diagnosed from the dashboard alone
  3. Every new/changed dashboard element passes the ui-consistency-reviewer's existing checks (design tokens, corner-radius, mono/tabular-nums on data) — no regressions on the existing visual bar

**Plans**: TBD
**UI hint**: yes

### Phase 5: Tenant Isolation Foundation

**Goal**: Each subscriber's trading runs in its own isolated environment — separate risk state, sizing, and kill switch — so one subscriber's activity never reads or writes another's.
**Depends on**: Nothing (first subscription phase)
**Requirements**: ISOL-01, ISOL-02, ISOL-03, ISOL-04
**Success Criteria** (what must be TRUE):

  1. Two subscribers' trades, risk state, and kill switches are provably separate — a test proves one subscriber's kill-switch trip never appears on another subscriber's account
  2. Subscriber can set their own position size (lots for India, $ margin for crypto) as an absolute value, safe under concurrent updates from multiple subscribers at once
  3. Subscriber can pause their own account's automated trading without affecting any other subscriber's trading
  4. When a subscriber's risk gate tightens their exposure, that subscriber sees the tightened state plainly on their own dashboard rather than it being applied silently

**Plans**: TBD
**UI hint**: yes

### Phase 6: Broker Connection & Credential Vault

**Goal**: A subscriber can securely connect their own broker account, with identity verified and credentials protected per subscriber.
**Depends on**: Phase 5
**Requirements**: BROK-01, BROK-02, BROK-03
**Success Criteria** (what must be TRUE):

  1. Subscriber can enter their own Dhan (India) or Delta Exchange India (crypto) API key/secret through a connect-broker form, never a shared account
  2. The system checks the entered credentials against the broker's own account-info endpoint before accepting the connection, catching a wrong-account mistake at connect time
  3. Broker credentials are stored encrypted and scoped to that one subscriber only, never written to a shared `.env` file

**Plans**: TBD
**UI hint**: yes

### Phase 7: Per-Subscriber Trading Safety

**Goal**: Every subscriber starts safe by default and can only arm real-money trading through the same explicit, two-lock process — scoped to their account alone.
**Depends on**: Phase 5, Phase 6
**Requirements**: SAFE-01, SAFE-02, SAFE-03
**Success Criteria** (what must be TRUE):

  1. A newly connected subscriber account starts in Paper mode by default
  2. Going live for one subscriber requires both independent locks (trading mode + live-trading flag) plus the exact arming phrase, and arms only that subscriber's account
  3. Switching a subscriber back to Paper disarms live trading for that subscriber immediately, with no exception

**Plans**: TBD

### Phase 8: Subscriber Reporting & Notifications

**Goal**: A subscriber can see and be notified about their own trading activity, broken out the same way the internal scorecard already works.
**Depends on**: Phase 5, Phase 6, Phase 7
**Requirements**: REPT-01, REPT-02, REPT-03
**Success Criteria** (what must be TRUE):

  1. Subscriber can view their own live P&L and trade history on their dashboard, and only their own
  2. Subscriber sees a per-(strategy, instrument) breakdown of their results instead of one blended equity curve
  3. Subscriber is notified via Telegram or an in-dashboard feed when their own trades open/close or a risk-gate/kill-switch event affects their account

**Plans**: TBD
**UI hint**: yes

### Phase 9: Subscription Billing

**Goal**: A subscriber can pay for, and manage, their own recurring subscription.
**Depends on**: Phase 5
**Requirements**: BILL-01, BILL-02, BILL-03
**Success Criteria** (what must be TRUE):

  1. Subscriber can subscribe to the one recurring paid plan
  2. Subscriber can cancel their own subscription at any time
  3. A failed payment is retried automatically and the subscriber is notified rather than silently dropped

**Plans**: TBD

### Phase 10: Compliance & Go-Live Gate

**Goal**: The platform meets the legal requirements that must be true before any non-Richard subscriber trades real money.
**Depends on**: Phase 6, Phase 7
**Requirements**: COMP-01, COMP-02
**Success Criteria** (what must be TRUE):

  1. Every subscriber's live trading traffic egresses through the platform's own whitelisted static IP, satisfying the SEBI static-IP requirement
  2. Every strategy shows a plain-language disclosure of what conditions trigger a trade, on a dedicated disclosure screen, before a subscriber can enable it

**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10.
Phases 1-4 have no dependencies on each other or on Phase 5+, so they could
run in parallel if that's preferred later — flagged here, not assumed.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Strategy Fixes | 4/4 | Complete    | 2026-09-30 |
| 2. Order Placing & Tracking | 7/7 | Complete    | 2026-10-01 |
| 3. Exit Optimisation | 6/6 | In Progress|  |
| 4. Dashboard UI/UX | 0/TBD | Not started | - |
| 5. Tenant Isolation Foundation | 0/TBD | Not started | - |
| 6. Broker Connection & Credential Vault | 0/TBD | Not started | - |
| 7. Per-Subscriber Trading Safety | 0/TBD | Not started | - |
| 8. Subscriber Reporting & Notifications | 0/TBD | Not started | - |
| 9. Subscription Billing | 0/TBD | Not started | - |
| 10. Compliance & Go-Live Gate | 0/TBD | Not started | - |
