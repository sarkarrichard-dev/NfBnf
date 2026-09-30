# Requirements: QuantHawk — Subscription Milestone

**Defined:** 2026-09-30
**Core Value:** Never presents a strategy as ready for real money until it has been measured — against real broker charges, on the real live journal — to actually make money.

Scope: the first paying outside subscriber. Full detail and reasoning behind
every line here lives in `.planning/research/FEATURES.md` (the research this
was drawn from) and `.planning/PROJECT.md`.

## v1 Requirements

Requirements for the first outside subscriber. Each maps to a roadmap phase.

### Broker Connection (BROK)

- [ ] **BROK-01**: Subscriber can connect their own Dhan (India) or Delta
  Exchange India (crypto) broker account by entering their own API
  key/secret — never a shared account.
- [ ] **BROK-02**: The system verifies a newly-connected account's identity
  against the broker's own account-info endpoint before accepting it —
  catches a wrong-account mistake at connect time, and is the same
  verification mechanism the referral-discount design already needs, built
  once and reused.
- [ ] **BROK-03**: Broker credentials are stored encrypted, scoped to one
  subscriber, never in a shared `.env` file.

### Tenant Isolation & Risk (ISOL)

- [ ] **ISOL-01**: Each subscriber's trading runs in its own isolated worker
  with its own risk state and kill switch — one subscriber's state never
  reads or writes another's.
- [ ] **ISOL-02**: Subscriber sets their own position sizing (lots for India,
  $ margin for crypto, matching the existing sizing convention) as an
  absolute value, not a delta, under the same read-modify-write lock
  discipline already required elsewhere in this codebase.
- [ ] **ISOL-03**: Subscriber has a visible "pause my trading" control that
  stops only their own account's new automated orders.
- [ ] **ISOL-04**: When a risk gate tightens a subscriber's exposure (not a
  full stop), that state is shown to the subscriber plainly, not applied
  silently.

### Trading Safety (SAFE)

- [ ] **SAFE-01**: A new subscriber account defaults to Paper mode.
- [ ] **SAFE-02**: Going live for a subscriber requires the existing two
  independent locks (trading mode + live-trading flag) plus the exact-phrase
  arming step, scoped per subscriber.
- [ ] **SAFE-03**: Switching a subscriber back to Paper always disarms live
  trading for that subscriber, with no exception.

### Subscriber Reporting (REPT)

- [ ] **REPT-01**: Subscriber can view their own live P&L and trade history,
  and only their own.
- [ ] **REPT-02**: Subscriber sees a per-(strategy, instrument) breakdown of
  their results, not one blended equity curve — a strategy that works on one
  instrument and not another should be visibly different, matching how the
  internal scorecard already works.
- [ ] **REPT-03**: Subscriber is notified (Telegram or an in-dashboard feed)
  when their own trades open/close and when a risk-gate or kill-switch event
  affects their account.

### Billing (BILL)

- [ ] **BILL-01**: Subscriber can subscribe to one recurring paid plan (a
  single flat tier is enough for v1 — no per-strategy pricing tiers yet).
- [ ] **BILL-02**: Subscriber can cancel their own subscription.
- [ ] **BILL-03**: A failed payment is retried and the subscriber is notified,
  not silently dropped.

### Compliance (COMP)

- [ ] **COMP-01**: All subscriber trading traffic egresses through the
  platform's own whitelisted static IP (the already-decided shared
  Elastic IP / NAT Gateway design) — this is a legal gate, not a feature
  choice.
- [ ] **COMP-02**: Every strategy offered to a subscriber has a plain-language
  disclosure of what conditions trigger a trade (white-box — not the source
  code, but not "proprietary secret" either), shown before the subscriber can
  enable it. Ships in v1 rather than retrofitted, since marketing a strategy
  as an undisclosed "black box" would push the product toward a SEBI
  Research-Analyst registration burden it doesn't have today.

## v2 Requirements

Deferred to after the first subscriber is live and validated.

### Trust & Growth

- **TRUST-01**: A public-facing page showing a strategy's real, verified
  track record (not a backtest, not a curated screenshot) — differentiator,
  not needed while the only subscribers already trust Richard directly.
- **TRUST-02**: Referral discount tied to verified broker identity (design
  already exists in `memory/referral-discount-verification.md`) — needs an
  actual second/third subscriber and a referral list to check against first.

### Billing Expansion

- **BILL2-01**: Multiple billing tiers (e.g. strategy-count-limited vs.
  unlimited) — worth doing once there's more than one strategy bundle to
  price separately.

### Notification Preferences

- **NOTF-01**: Per-subscriber notification channel/preferences, replacing a
  single shared bot — only matters once more than one subscriber would
  actually collide on it.

### Longer-Term (v2+, not yet scheduled)

- **FUT-01**: Multi-broker choice per segment (Zerodha/Groww for India,
  CoinDCX for crypto) — deliberately deferred per the existing Broker
  Adapter Plan; real engineering cost, only valuable once subscribers show
  up who specifically lack a Dhan/Delta account.
- **FUT-02**: Exchange-issued Algo ID registration and per-strategy SEBI
  filing pipeline — becomes mandatory once subscriber order volume crosses
  the exemption threshold. Needs its own dedicated legal/research pass
  against the current SEBI circular text before scaling past a handful of
  subscribers; do not treat "we're small so it's exempt" as a permanent
  answer.

## Out of Scope

Explicitly excluded, with reasoning, so these don't get quietly re-proposed.

| Feature | Reason |
|---------|--------|
| Pooled / "we trade on your behalf from our own master account" model | Crosses into investment-adviser / portfolio-manager territory under SEBI on top of the black-box RA-license trigger; also concentrates custody risk this project doesn't want. Each subscriber's own broker account, always. |
| Marketing any strategy as an undisclosed "proprietary black box" | Triggers SEBI's Research Analyst registration requirement for undisclosed algos — a real licensing burden, not a formality. |
| ML auto-scaling a subscriber's live position size without an explicit subscriber-set cap | Conflicts with this project's own standing ML guardrails (confidence ladder, human approval, never auto-apply to a currently-profitable strategy) and with subscriber-controlled sizing being table stakes. |
| Raw returns leaderboard ranking subscribers/strategies | Rewards short-window luck over the measured readiness bar already used internally; invites the survivorship-biased marketing this project has explicitly rejected. |
| One-click "connect all your brokers" via credential-sharing or screen-scraping | Bypasses SEBI's OAuth/2FA/unique-API-key mandate and is a security anti-pattern (storing a raw broker password instead of a scoped API key). |
| Treating the small-platform order-rate exemption as a permanent skip on Algo ID registration | The exemption is a per-client order-rate threshold, not a blanket exemption for the platform — it stops applying as soon as volume crosses it. Flag it, don't quietly build around it. |
| A single kill switch shared across every subscriber | Violates the per-tenant isolation principle already decided for multi-tenancy — one subscriber's bad day must never freeze another's account. |

## Traceability

Filled in during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| BROK-01 | — | Pending |
| BROK-02 | — | Pending |
| BROK-03 | — | Pending |
| ISOL-01 | — | Pending |
| ISOL-02 | — | Pending |
| ISOL-03 | — | Pending |
| ISOL-04 | — | Pending |
| SAFE-01 | — | Pending |
| SAFE-02 | — | Pending |
| SAFE-03 | — | Pending |
| REPT-01 | — | Pending |
| REPT-02 | — | Pending |
| REPT-03 | — | Pending |
| BILL-01 | — | Pending |
| BILL-02 | — | Pending |
| BILL-03 | — | Pending |
| COMP-01 | — | Pending |
| COMP-02 | — | Pending |

**Coverage:**
- v1 requirements: 18 total
- Mapped to phases: 0
- Unmapped: 18 ⚠️ (roadmap not yet created)

---
*Requirements defined: 2026-09-30*
*Last updated: 2026-09-30 after initial definition, drawn from `.planning/research/FEATURES.md`*
