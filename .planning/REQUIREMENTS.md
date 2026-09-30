# Requirements: QuantHawk — Subscription Milestone

**Defined:** 2026-09-30
**Core Value:** Never presents a strategy as ready for real money until it has been measured — against real broker charges, on the real live journal — to actually make money.

Scope: getting the existing product itself solid (strategy quality, order
handling, exits, dashboard UX) and getting it ready for the first paying
outside subscriber. Full detail and reasoning behind the subscription-related
lines lives in `.planning/research/FEATURES.md`; the product-quality lines
are grounded in `.planning/codebase/CONCERNS.md` and this project's own
memory (`strategy-findings.md`, `strategy-analysis-and-simplification-directive.md`).

## v1 Requirements

Requirements for the first outside subscriber. Each maps to a roadmap phase.

### Strategy Fixes (STRAT)

- [ ] **STRAT-01**: The option buy lane's entries are reworked to raise its
  real (live-journal) win rate toward Richard's ≥65% precision bar — the
  acknowledged weak lane (`memory/strategy-findings.md`,
  `memory/strategy-analysis-and-simplification-directive.md`). Phase 1 built
  the tightening gates and proved the measuring tools work, but Richard's
  own decision on the real-data test was Hold (see
  `.planning/phases/01-strategy-fixes/01-04-SUMMARY.md`) — nothing is
  switched on, so the win rate has not actually moved. Stays open for a
  future attempt.
- [x] **STRAT-02**: Any strategy-parameter change goes through the existing
  confidence ladder (~15 trades observe-only, ~40+ trades human-approved
  suggestion) — no shortcutting the ladder to ship a "fix" faster.
- [x] **STRAT-03**: Strategy Lab verdicts (COLLECTING/PASSING/DROPPED) are
  re-validated against the real recorded option-chain data available since
  2026-09-23 — anything scored before that date used the Black-Scholes proxy
  and is not trustworthy as-is (`.planning/codebase/CONCERNS.md`).

### Order Placing & Tracking (ORD)

- [ ] **ORD-01**: Order placement handles a cancel-issued-while-placing race
  without creating a duplicate or an orphaned order (currently untested,
  `.planning/codebase/CONCERNS.md` — money-path edge cases).
- [ ] **ORD-02**: A position is correctly reconciled after a broker
  disconnection — no stale or duplicate position left in the journal.
- [ ] **ORD-03**: The Dhan websocket reconnects and recovers missed ticks
  during a busy scan cycle (20+ concurrent candles) without silently
  dropping data a stop-trigger depends on.
- [ ] **ORD-04**: Tick-driven stop triggering degrades safely to the
  candle-based fallback when the tick feed lags or drops, and this fallback
  behavior is documented, not just known by whoever wrote it.

### Exit Optimisation (EXIT)

- [ ] **EXIT-01**: Trailing-stop point values (NIFTY/BANKNIFTY/SENSEX, buy
  and sell lanes) are re-validated against recent live-journal data on a
  recurring basis instead of staying a one-time hardcoded tune from
  2026-09-24/28.
- [ ] **EXIT-02**: The dead `premium_trail.py` percent-of-premium code path
  is removed so there is exactly one, unambiguous trailing-stop
  implementation in the codebase.
- [ ] **EXIT-03**: A monitoring signal exists if trail-stop hit rate or win
  rate drifts meaningfully from its last validated baseline, so a bad tune
  is caught rather than discovered weeks later in the scorecard.

### Dashboard UI/UX (UIUX)

- [ ] **UIUX-01**: The dashboard shows which (strategy, coin) pairs are
  actually live vs. paper after arming crypto — today this requires reading
  server logs to find out (`.planning/codebase/CONCERNS.md`).
- [ ] **UIUX-02**: A "Data Health" view shows tick age, option-chain
  snapshot age, measured spread age, and Dhan websocket status, so a missed
  trade or a stop that didn't fire can be diagnosed without reading logs.
- [ ] **UIUX-03**: Every dashboard change continues to meet QuantHawk's
  existing visual bar — design tokens (not raw colors), correct
  corner-radius, mono/tabular-nums on data — the standard the
  ui-consistency-reviewer already enforces.

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

| Requirement | Phase | Status |
|-------------|-------|--------|
| STRAT-01 | Phase 1 | Pending — Hold decision, see 01-04-SUMMARY.md |
| STRAT-02 | Phase 1 | Complete |
| STRAT-03 | Phase 1 | Complete |
| ORD-01 | Phase 2 | Pending |
| ORD-02 | Phase 2 | Pending |
| ORD-03 | Phase 2 | Pending |
| ORD-04 | Phase 2 | Pending |
| EXIT-01 | Phase 3 | Pending |
| EXIT-02 | Phase 3 | Pending |
| EXIT-03 | Phase 3 | Pending |
| UIUX-01 | Phase 4 | Pending |
| UIUX-02 | Phase 4 | Pending |
| UIUX-03 | Phase 4 | Pending |
| BROK-01 | Phase 6 | Pending |
| BROK-02 | Phase 6 | Pending |
| BROK-03 | Phase 6 | Pending |
| ISOL-01 | Phase 5 | Pending |
| ISOL-02 | Phase 5 | Pending |
| ISOL-03 | Phase 5 | Pending |
| ISOL-04 | Phase 5 | Pending |
| SAFE-01 | Phase 7 | Pending |
| SAFE-02 | Phase 7 | Pending |
| SAFE-03 | Phase 7 | Pending |
| REPT-01 | Phase 8 | Pending |
| REPT-02 | Phase 8 | Pending |
| REPT-03 | Phase 8 | Pending |
| BILL-01 | Phase 9 | Pending |
| BILL-02 | Phase 9 | Pending |
| BILL-03 | Phase 9 | Pending |
| COMP-01 | Phase 10 | Pending |
| COMP-02 | Phase 10 | Pending |

**Coverage:**

- v1 requirements: 31 total
- Mapped to phases: 31
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-30*
*Last updated: 2026-09-30 — added Strategy Fixes / Order Placing & Tracking /
Exit Optimisation / Dashboard UI-UX (Richard, same day) as Phases 1-4, ahead
of the subscription build-out (now Phases 5-10). All 31 v1 requirements
mapped, see `.planning/ROADMAP.md`.*
