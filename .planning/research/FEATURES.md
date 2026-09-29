# Feature Research

**Domain:** Subscription algo-trading / signal-copying SaaS (India-focused: NSE/BSE index options, MCX commodities via Dhan; crypto perps/options via Delta Exchange India)
**Researched:** 2026-09-30
**Confidence:** MEDIUM (regulatory claims cross-checked across multiple independent secondary sources — treat as MEDIUM-HIGH, not primary-source HIGH; verify against the actual SEBI circular text before building the compliance-gating code). Competitor feature claims are blog-sourced, MEDIUM.

## Feature Landscape

### Table Stakes (Users Expect These)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Broker-account connect flow (API key + secret entry, live validation against the broker's own profile/account endpoint) | Every algo platform in this space (Streak, Tradetron, Collective2) starts here — a subscriber can't do anything until their own broker is linked | MEDIUM | QuantHawk already has this pattern once (Dhan single-tenant). Multi-tenant version needs per-user encrypted storage (AWS Secrets Manager, per Key Decisions) not `.env`. **Verify with the broker's own account-identity endpoint at connect time** — same mechanism Richard already designed for referral-fraud prevention (`memory/referral-discount-verification.md`) does double duty here: it stops "connected the wrong account" mistakes too, not just fraud. |
| Paper vs. live mode toggle, visible and subscriber-controlled | Every platform researched (Streak, Tradetron) lets a user test before risking money; QuantHawk's own core value ("never presents a strategy as ready for real money until measured") makes this non-negotiable | LOW | Already exists single-tenant (`TRADING_MODE`); needs to become per-subscriber state, not a server-global |
| Subscriber-visible kill switch / pause-my-trading button | Every regulated retail platform gives the account holder a way to immediately stop new automated orders on their own account — table stakes for money-path trust, and matches QuantHawk's existing kill-switch precedent (`risk_manager.py`'s `STOPPED` state) | LOW-MEDIUM | Must act at the subscriber's own automation only — don't let one subscriber's panic button touch another's positions. Existing `risk.kill_switch_state` logic is a good template, just needs to be keyed per subscriber |
| Position sizing controls the subscriber sets (lots for India, $ margin for crypto — already decided in Key Decisions) | Nobody hands a stranger control of their capital without a sizing dial; also the existing lot/margin sizing convention already fits a subscriber-facing settings page | MEDIUM | Read-modify-write on this needs the lock discipline already called out in CLAUDE.md ("client should send an absolute value, not a delta") |
| Live P&L / trade history per subscriber | Users must see their own results — this is what the existing Strategy P&L scorecard and Trade History tabs already do single-tenant; multi-tenant just means scoping it | MEDIUM | Mostly an extension of `strategy_performance.py`'s existing per-(strategy,instrument) scorecard, filtered by subscriber |
| Subscription billing (recurring charge, plan tiers, upgrade/downgrade, failed-payment handling) | Baseline SaaS expectation — no subscriber pays into a product with no visible billing management | MEDIUM-HIGH | India-specific: needs a payment gateway that handles UPI/cards for INR (Razorpay/Stripe India), not a US-only processor |
| Notifications on trade open/close, and on risk-gate/kill-switch events | Already exists single-tenant via Telegram; a subscriber who can't see what their own algo is doing in real time won't trust it | LOW | Needs per-subscriber Telegram chat ID or an in-dashboard notification feed instead of one shared bot |
| Mobile-usable dashboard (not necessarily a native app, but responsive) | Traders check positions from their phone constantly during market hours; every competitor platform (Streak, Tradetron) has a mobile-usable view | MEDIUM | QuantHawk dashboard is already held to "high UX bar" per CLAUDE.md; extend, don't rebuild |
| Static-IP / API-whitelisting compliance path per subscriber's broker account | **This is a legal requirement, not a nice-to-have** — SEBI's 2025-26 framework requires retail API trading to originate from a broker-whitelisted static IP; brokers began rejecting non-whitelisted calls April 2026 | MEDIUM | QuantHawk's planned architecture (one shared Elastic IP for all worker containers) already satisfies this for every subscriber at once — confirmed still the right shape, don't redesign it |

### Differentiators (Competitive Advantage)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Real, verified live-journal track record per strategy (not a backtest, not a curated screenshot) | This is QuantHawk's actual Core Value already — "never presents a strategy as ready... until measured on the real live journal." Competitor platforms (Streak, Tradetron) mostly show self-reported or backtested numbers; Collective2/myfxbook's whole business model is that *verified* (broker-confirmed) track records are rare and trusted. Showing prospective subscribers the same real-charges, real-journal numbers Richard already insists on internally is a genuine trust edge, not new work | LOW (mostly a public-facing view of data that already exists) | The "Strategy Lab" verdict system (COLLECTING / PASSING / DROPPED) is a differentiator by itself if surfaced to prospects — most competitors don't show "this strategy failed and we killed it," which is more credible than only ever showing winners |
| Per-(strategy, instrument) transparency, not a single blended equity curve | Richard's own standing direction (`strategy-analysis-and-simplification-directive.md`) — most copy-trading platforms show one aggregate P&L line, which hides that a strategy works on NIFTY and not BANKNIFTY. Showing subscribers the real per-instrument breakdown is unusual and defensible given it's already how the internal scorecard works | LOW | Reuse, don't rebuild |
| Readiness-gated strategy rollout shown to subscribers ("this strategy needs 30 more trades before we'd trust it live") | Turns an internal discipline (the 30-trade/14-day readiness bar) into a subscriber-facing signal of rigor — few retail algo platforms explain *why* a strategy isn't live yet | LOW | Pure surfacing of existing `strategy_performance.crypto_live_pairs()` / scorecard logic |
| Honest "no edge" reporting for retired strategies | `strategy-findings.md` shows a track record of killing strategies that didn't work (naked buying, social-media indicators, sideways structures). Publishing a short "what we tried and killed" page is unusual credibility-building in a space full of survivorship-biased marketing | LOW | Content/curation work, not engineering |
| Multi-broker choice per segment (Dhan/Zerodha/Groww for India; Delta/CoinDCX for crypto) | Already a stated future direction (`multi-broker-architecture-direction.md`) — lets a subscriber use the broker they already have an account with instead of forcing a new broker signup, which is real friction reduction | HIGH | Deliberately sequenced last per the existing Broker Adapter Plan (crypto second-broker first, India second-broker tied to the credential-vault/multi-tenancy timeline) — don't pull this earlier than that plan says |
| Tightens-not-halts risk response, visible to the subscriber | Existing standing behavior (`risk-gates-tighten-not-stop`) — most retail platforms just cut you off entirely under stress. Showing the subscriber "your risk was tightened to 1 lot, not stopped" as an explained, visible state (rather than a silent internal flag) is a differentiator in trust and in avoiding needless subscriber panic/churn | LOW-MEDIUM | Needs a dashboard surface for the existing `TIGHTENED` state, not new logic |
| Referral discount tied to verified broker identity (not self-reported) | Already designed (`referral-discount-verification.md`) — using the broker's own account-ID response as the fraud-proof source of truth is a cleaner mechanism than most referral programs, which just trust a signup form | MEDIUM | Automatable half already speced; the broker-side referral-list matching stays a manual CSV import from Richard — don't try to automate that half, it isn't possible via either broker's API today |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Pooled-account / "we trade on your behalf with our own master account" model | Simpler to build than per-subscriber broker connections; some copy-trading platforms work this way | This crosses into portfolio-manager / investment-adviser territory under SEBI, on top of the RA-license trigger for black-box algos — a materially bigger regulatory burden than "software that places orders a subscriber's own broker account, under the subscriber's own consent." It also concentrates custody risk QuantHawk doesn't want | Keep the already-decided model: each subscriber connects their **own** broker account, their own money never touches a QuantHawk-controlled account |
| Undisclosed / "black box" strategy logic marketed as a differentiator ("our secret proprietary algo") | Feels like it protects IP and sounds impressive in marketing copy | Under SEBI's 2025-26 framework, an algo whose logic is not disclosed to the user is explicitly a "black box" algo, and offering one to retail requires the provider to register as a **SEBI Research Analyst** — a real licensing and compliance burden, not a formality. This is the single sharpest India-specific compliance trap in this whole research pass | Ship **white-box** algos: disclose the strategy logic/rules to the subscriber (doesn't mean open-sourcing the code, means the subscriber can see what conditions trigger a trade) and register the strategy with the exchange once, which is the lighter-weight compliant path |
| Auto-scaling a subscriber's live position size based on an internal ML confidence score, without an explicit subscriber-set cap | Feels like a "smart" differentiator — let the system size up when it's more confident | Conflicts directly with QuantHawk's own already-litigated ML guardrails (`strategy-analysis-and-simplification-directive.md`: confidence ladder, human approval, never auto-apply to a currently-profitable strategy) and with giving subscribers a sizing dial they actually control (table stakes above). Also a fast way to blow past a subscriber's own risk tolerance without them noticing | Keep sizing subscriber-set (lots / $ margin), let ML *suggest* changes for human review exactly as the existing internal roadmap already specifies — don't let it act unilaterally on subscriber capital |
| Real-time "leaderboard" ranking subscribers or strategies against each other by short-window returns | Common in copy-trading/social-trading platforms (Collective2-style rankings), feels engaging | Rewards short-window luck over the measured, multi-week readiness bar QuantHawk already insists on internally; also invites exactly the kind of survivorship-biased, screenshot-driven marketing Richard has explicitly rejected for strategy evaluation ("the screenshot always shows the winners; the backtest shows all of it") | If any comparative view ships, anchor it to the same readiness-gated, real-cost, per-instrument numbers already used internally — not a raw returns leaderboard |
| One-click "connect all my brokers with one login" via a screen-scraping or credential-sharing shortcut | Reduces onboarding friction | Bypasses the OAuth-based, 2FA, broker-issued-API-key flow SEBI's framework specifically mandates for algo API access; also a security anti-pattern (storing a subscriber's raw broker login/password instead of a scoped API key) | Use each broker's actual OAuth/API-key issuance flow, one broker connection at a time, exactly as SEBI's static-IP + unique-API-key requirement assumes |
| Skipping per-subscriber Algo ID / strategy registration because "we're under the 10-orders/second exemption threshold today" | Tempting shortcut while subscriber count and order volume are still small | The exemption is a per-client, per-exchange order-rate threshold, not a blanket exemption for the platform — and it stops applying the moment a subscriber (or the platform in aggregate, depending on final circular language) crosses it. Building the subscription product without a plan for exchange-issued Algo ID tagging is designing in a compliance debt that gets worse, not better, as the subscriber base grows | Treat "which strategies need exchange registration and Algo ID tagging, and at what volume" as a hard-gate research question before scaling past a handful of subscribers — flag it back to Richard explicitly, don't quietly build around it |
| A single shared kill switch that stops every subscriber's trading at once when triggered by one subscriber's risk state | Simpler than per-subscriber state | Directly violates the isolation principle already decided for multi-tenancy ("isolation is a safety property for money-path software") — one subscriber's bad day should never freeze another's account | Per-subscriber risk state and kill switch, inside the isolated-worker-per-customer architecture already decided |

## Feature Dependencies

```
Per-subscriber broker connection (identity-verified via broker account API)
    └──requires──> Per-user encrypted credential storage (AWS Secrets Manager)
                       └──requires──> Multi-tenancy foundation (Postgres, containerised workers, tenant_id)

Subscriber-visible kill switch / position sizing
    └──requires──> Per-subscriber risk state (not module-global — current single-tenant state blocks this)

Subscription billing
    └──requires──> Per-user account/auth system (doesn't exist yet — currently single ADMIN_API_SECRET, not per-user)

Live P&L / Strategy P&L shown to a subscriber
    └──requires──> Per-subscriber trade journal (Postgres or per-tenant SQLite, not the single shared trade_memory.sqlite)

Public-facing verified track record (differentiator)
    └──enhances──> Subscriber acquisition, but ──requires──> the per-(strategy,instrument) scorecard already existing (it does)

Referral discount tied to verified broker identity
    └──requires──> Per-subscriber broker connection (same identity-verification step, reused)

White-box strategy disclosure + exchange registration
    └──requires──> Deciding, per strategy, whether its logic counts as "disclosed" under SEBI's framework
                       └──conflicts──> Marketing any strategy as "proprietary/secret" (that framing pushes it toward black-box / RA-license territory)

Multi-broker choice per segment (differentiator)
    └──requires──> Multi-tenancy + credential vault (same dependency chain as broker connection above)
    └──conflicts (near-term)──> Building it before the credential vault exists — already sequenced last in the Broker Adapter Plan, don't pull forward
```

### Dependency Notes

- **Almost everything subscriber-facing requires multi-tenancy first.** The single biggest fact this research surfaces for roadmap ordering: broker connection, kill switches, billing, live P&L, and referral discounts all sit on top of "per-subscriber state exists" — which today it does not (single `.env`, single SQLite, module-global risk state, per PROJECT.md's own Out of Scope list). Any phase plan that tries to ship subscriber-facing features before the multi-tenancy foundation will hit this wall immediately.
- **Broker identity verification is a shared building block, not a one-off.** The same "call the broker's own account endpoint, trust that response, not a self-reported label" mechanism is needed for onboarding *and* referral-fraud prevention *and* the SEBI unique-API-key requirement. Build it once, in the connection flow, and reuse it — don't build a separate check for referrals later.
- **White-box disclosure conflicts with "secret sauce" marketing.** This is the one real product-positioning tension in the whole feature set: the instinct to market a strategy as proprietary/undisclosed pushes it toward the SEBI black-box/Research-Analyst path, which is a heavier compliance lift than QuantHawk's current single-person operation likely wants to take on. Decide this deliberately, not by accident of marketing copy.

## MVP Definition

### Launch With (v1)

Minimum viable product for the *first* paying outside subscriber — not the full multi-tenant SaaS vision.

- [ ] Per-subscriber broker connection (Dhan for India, Delta for crypto — the two already supported) with identity verification at connect time — essential, this is the entire premise of "subscribers connect their own broker"
- [ ] Per-subscriber isolated worker (per the already-decided architecture) with its own risk state, kill switch, and position sizing — essential for money-path safety and for the subscriber-visible kill switch to mean anything
- [ ] Paper mode default, explicit arming step to go live (reusing the existing two-lock + exact-phrase pattern) — essential, matches QuantHawk's own non-negotiable safety posture
- [ ] Subscriber-visible live P&L / trade history for their own account (reusing the existing scorecard logic) — essential, a subscriber paying for an algo they can't see the results of won't stay
- [ ] Basic subscription billing (one plan, recurring charge, cancel) — essential to actually be a subscription product, doesn't need tiers yet
- [ ] Static-IP compliant architecture (the shared Elastic IP, already decided) — essential, this is a legal gate not a feature choice
- [ ] White-box disclosure of what each offered strategy does (in plain terms, not source code) — essential given the black-box/RA-license trap above; cheaper to build this in from day one than retrofit it

### Add After Validation (v1.x)

- [ ] Public-facing verified track record / strategy marketing page — once there's a real subscriber-facing track record worth showing (differentiator, not needed for the first subscriber who already trusts Richard directly)
- [ ] Referral discount + broker-identity matching — once there's an actual second/third subscriber to refer, and Richard has a referral list worth checking against
- [ ] Multiple billing tiers (e.g., strategy-count-limited vs. unlimited) — once there's more than one strategy bundle worth pricing separately
- [ ] Per-subscriber Telegram/notification preferences — once there's more than one subscriber sharing a notification channel would even be a problem

### Future Consideration (v2+)

- [ ] Multi-broker choice per segment (Zerodha/Groww/CoinDCX etc.) — deliberately deferred per the existing Broker Adapter Plan sequencing; real engineering cost, and only valuable once there are subscribers who specifically don't have Dhan/Delta accounts
- [ ] Comparative strategy views / leaderboards — defer indefinitely unless redesigned around the readiness-gated, real-cost numbers already used internally; a naive leaderboard is an anti-feature (see above)
- [ ] Exchange-issued Algo ID registration and per-strategy SEBI filing — becomes mandatory once subscriber order volume crosses the exemption threshold; needs its own dedicated research pass against the actual current SEBI circular text before scaling, don't leave it as a v1 unknown

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Per-subscriber broker connection + identity verification | HIGH | MEDIUM | P1 |
| Isolated per-subscriber worker (risk state, kill switch, sizing) | HIGH | HIGH | P1 |
| Paper/live toggle with existing two-lock arming | HIGH | LOW | P1 |
| Subscriber-visible live P&L | HIGH | MEDIUM | P1 |
| Basic subscription billing | HIGH | MEDIUM | P1 |
| Static-IP-compliant hosting architecture | HIGH (legal gate) | MEDIUM (already decided, needs building) | P1 |
| White-box strategy disclosure | HIGH (legal/trust) | LOW | P1 |
| Public verified track record page | MEDIUM | LOW | P2 |
| Referral discount w/ broker-identity matching | MEDIUM | MEDIUM | P2 |
| Multiple billing tiers | MEDIUM | LOW-MEDIUM | P2 |
| Multi-broker choice per segment | MEDIUM | HIGH | P3 |
| Comparative/leaderboard views | LOW (risk of misuse) | MEDIUM | P3 |
| Exchange Algo ID registration pipeline | HIGH once triggered | HIGH | P3 (P1 the moment volume threshold is near) |

**Priority key:**
- P1: Must have for the first paying outside subscriber
- P2: Should have once there's more than one subscriber
- P3: Defer until subscriber count or order volume justifies the cost (or, for Algo ID registration, until legally required)

## Competitor Feature Analysis

| Feature | Streak (Zerodha) | Tradetron | Collective2 / myfxbook | QuantHawk's Approach |
|---------|-------------------|-----------|--------------------------|------------------------|
| Broker lock-in | Zerodha-account holders only | Multi-broker (50+ brokers) | Broker-agnostic, links to user's own brokerage | Dhan + Delta today, multi-broker per segment planned but deliberately deferred (see dependencies) |
| Strategy building | No-code visual builder for the subscriber | No-code visual builder + marketplace to buy/sell strategies | Manager-led — subscriber follows a manager's signals, doesn't build their own | Subscriber does not build strategies; they subscribe to QuantHawk's own measured strategies — closer to Collective2's model than Streak/Tradetron's DIY model |
| Track record trust | Platform-reported | Platform-reported | **Independently verified** by connecting to the subscriber's real broker account — the trust anchor of the whole platform | Match this: QuantHawk's real-journal, real-charges numbers are already more rigorous than most competitors' — the gap is only in *surfacing* them publicly |
| Pricing model | Free with Zerodha account | Free tier to $300/mo, unlimited strategies at top tier | Subscription fee to follow a strategy, split between provider and platform | Not yet decided — MVP should keep it simple (flat recurring fee) rather than copying Tradetron's tiered-by-strategy-count model prematurely |
| Compliance posture | Strategies registered per SEBI framework (Zerodha as broker handles much of this) | Multi-broker means compliance burden is more distributed/complex | Not India-specific, doesn't face SEBI's black-box/RA-license rules at all | Must build white-box disclosure and static-IP compliance in from v1 — QuantHawk doesn't have a broker's built-in compliance scaffolding, it *is* the algo provider |

## Sources

- [SEBI Regulations on Algorithmic Trading in India — Groww](https://groww.in/blog/sebi-regulations-on-algorithmic-trading-in-india)
- [Explaining the latest SEBI algo trading regulations — Zerodha](https://zerodha.com/z-connect/business-updates/explaining-the-latest-sebi-algo-trading-regulations)
- [SEBI Algo Trading Rules 2025: What Indian Traders Must Know — Motilal Oswal](https://www.motilaloswal.com/learning-centre/2025/6/sebi-regulations-on-algorithmic-trading-in-india)
- [Retail algo trading set for August 2025 rollout under SEBI's new framework — Upstox](https://upstox.com/news/business-news/latest-updates/sebi-opens-doors-for-retail-investors-in-algo-trading-with-new-regulatory-framework/article-144719/)
- [SEBI Circular: Safeguarding Retail Investors In Algorithmic Trading — Mondaq](https://www.mondaq.com/india/commoditiesderivativesstock-exchanges/1581380/sebi-circular-safeguarding-retail-investors-in-algorithmic-trading)
- [Static IP for API Trading: Setup, Requirements & Fixes — HDFC Sky](https://hdfcsky.com/sky-learn/algo-trading/what-is-static-ip-api-trading)
- [SEBI's Static IP Mandate Is Live — QuotaGuard](https://www.quotaguard.com/blog/sebis-static-ip-mandate-is-live-fix-your-cloud-trading-bot-now)
- [SEBI Algo Trading Rules 2025–2026 — AlgoBulls](https://algobulls.com/blog/industry-insights-and-updates/sebi-new-algotrading-regulations-for-retail-investors-2026)
- [8 Best Algo Trading Platforms in India (2026) — AlgoTest](https://algotest.in/blog/8-best-algo-trading-platforms-in-india-2026/)
- [Streak vs. Tradetron: A Comprehensive Comparison — AlgoTest](https://algotest.in/blog/streak-vs-tradetron/)
- [Tradetron features](https://tradetron.tech/pages/features)
- [Collective2 — track record verification](https://trade.collective2.com/)
- [Myfxbook verification](https://help.myfxbook.com/knowledge-base/verification/)
- [10 Must-Have Copy Trading Platform Features — Brokeree](https://brokeree.com/articles/10-must-have-copy-trading-features)
- Internal project memory: `memory/referral-discount-verification.md`, `memory/multi-broker-architecture-direction.md`, `memory/project-algo-bnf-vision.md`, `memory/strategy-analysis-and-simplification-directive.md`, `memory/risk-gates-tighten-not-stop.md`, `memory/strategy-findings.md`

---
*Feature research for: Subscription algo-trading SaaS (India options/futures + crypto perps/options)*
*Researched: 2026-09-30*
