# Roadmap: QuantHawk — Subscription Milestone

## Overview

This roadmap takes QuantHawk from a single-operator app to a platform that can
safely hold a first paying outside subscriber's money. It starts by extracting
the module-global state that today assumes one user into genuinely isolated
per-subscriber risk, sizing, and kill-switch state (Phase 1) — the dependency
root every later phase sits on, since broker connections, safety locks,
reporting, and billing all need "per-subscriber" to already be a real thing.
From there it layers on broker connection and credential storage (Phase 2),
the existing paper/live safety locks scoped per subscriber (Phase 3),
subscriber-facing reporting and notifications (Phase 4), subscription billing
(Phase 5), and finally the legal go-live gate — static-IP egress and
strategy disclosure — that must hold before any non-Richard subscriber trades
real money (Phase 6).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Tenant Isolation Foundation** - Per-subscriber risk state, sizing, kill switch, and pause control, fully separated from every other subscriber's
- [ ] **Phase 2: Broker Connection & Credential Vault** - Subscriber connects and verifies their own broker account; credentials encrypted and scoped to them alone
- [ ] **Phase 3: Per-Subscriber Trading Safety** - Paper-by-default and two-lock live arming, scoped to each subscriber's own account
- [ ] **Phase 4: Subscriber Reporting & Notifications** - Subscriber sees their own P&L, per-(strategy, instrument) breakdown, and trade/risk notifications
- [ ] **Phase 5: Subscription Billing** - Subscribe, cancel, and failed-payment handling for one flat recurring plan
- [ ] **Phase 6: Compliance & Go-Live Gate** - Static-IP egress and white-box strategy disclosure verified before real-money go-live

## Phase Details

### Phase 1: Tenant Isolation Foundation
**Goal**: Each subscriber's trading runs in its own isolated environment — separate risk state, sizing, and kill switch — so one subscriber's activity never reads or writes another's.
**Depends on**: Nothing (first phase)
**Requirements**: ISOL-01, ISOL-02, ISOL-03, ISOL-04
**Success Criteria** (what must be TRUE):
  1. Two subscribers' trades, risk state, and kill switches are provably separate — a test proves one subscriber's kill-switch trip never appears on another subscriber's account
  2. Subscriber can set their own position size (lots for India, $ margin for crypto) as an absolute value, safe under concurrent updates from multiple subscribers at once
  3. Subscriber can pause their own account's automated trading without affecting any other subscriber's trading
  4. When a subscriber's risk gate tightens their exposure, that subscriber sees the tightened state plainly on their own dashboard rather than it being applied silently
**Plans**: TBD
**UI hint**: yes

### Phase 2: Broker Connection & Credential Vault
**Goal**: A subscriber can securely connect their own broker account, with identity verified and credentials protected per subscriber.
**Depends on**: Phase 1
**Requirements**: BROK-01, BROK-02, BROK-03
**Success Criteria** (what must be TRUE):
  1. Subscriber can enter their own Dhan (India) or Delta Exchange India (crypto) API key/secret through a connect-broker form, never a shared account
  2. The system checks the entered credentials against the broker's own account-info endpoint before accepting the connection, catching a wrong-account mistake at connect time
  3. Broker credentials are stored encrypted and scoped to that one subscriber only, never written to a shared `.env` file
**Plans**: TBD
**UI hint**: yes

### Phase 3: Per-Subscriber Trading Safety
**Goal**: Every subscriber starts safe by default and can only arm real-money trading through the same explicit, two-lock process — scoped to their account alone.
**Depends on**: Phase 1, Phase 2
**Requirements**: SAFE-01, SAFE-02, SAFE-03
**Success Criteria** (what must be TRUE):
  1. A newly connected subscriber account starts in Paper mode by default
  2. Going live for one subscriber requires both independent locks (trading mode + live-trading flag) plus the exact arming phrase, and arms only that subscriber's account
  3. Switching a subscriber back to Paper disarms live trading for that subscriber immediately, with no exception
**Plans**: TBD

### Phase 4: Subscriber Reporting & Notifications
**Goal**: A subscriber can see and be notified about their own trading activity, broken out the same way the internal scorecard already works.
**Depends on**: Phase 1, Phase 2, Phase 3
**Requirements**: REPT-01, REPT-02, REPT-03
**Success Criteria** (what must be TRUE):
  1. Subscriber can view their own live P&L and trade history on their dashboard, and only their own
  2. Subscriber sees a per-(strategy, instrument) breakdown of their results instead of one blended equity curve
  3. Subscriber is notified via Telegram or an in-dashboard feed when their own trades open/close or a risk-gate/kill-switch event affects their account
**Plans**: TBD
**UI hint**: yes

### Phase 5: Subscription Billing
**Goal**: A subscriber can pay for, and manage, their own recurring subscription.
**Depends on**: Phase 1
**Requirements**: BILL-01, BILL-02, BILL-03
**Success Criteria** (what must be TRUE):
  1. Subscriber can subscribe to the one recurring paid plan
  2. Subscriber can cancel their own subscription at any time
  3. A failed payment is retried automatically and the subscriber is notified rather than silently dropped
**Plans**: TBD

### Phase 6: Compliance & Go-Live Gate
**Goal**: The platform meets the legal requirements that must be true before any non-Richard subscriber trades real money.
**Depends on**: Phase 2, Phase 3
**Requirements**: COMP-01, COMP-02
**Success Criteria** (what must be TRUE):
  1. Every subscriber's live trading traffic egresses through the platform's own whitelisted static IP, satisfying the SEBI static-IP requirement
  2. Every strategy shows a plain-language disclosure of what conditions trigger a trade, on a dedicated disclosure screen, before a subscriber can enable it
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Tenant Isolation Foundation | 0/TBD | Not started | - |
| 2. Broker Connection & Credential Vault | 0/TBD | Not started | - |
| 3. Per-Subscriber Trading Safety | 0/TBD | Not started | - |
| 4. Subscriber Reporting & Notifications | 0/TBD | Not started | - |
| 5. Subscription Billing | 0/TBD | Not started | - |
| 6. Compliance & Go-Live Gate | 0/TBD | Not started | - |
