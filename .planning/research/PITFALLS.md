# Pitfalls Research

**Domain:** Multi-tenant algo-trading SaaS (India options/futures via Dhan, crypto via Delta Exchange India, MCX commodities) — converting an existing single-tenant, real-money trading app to subscription distribution
**Researched:** 2026-09-30
**Confidence:** MEDIUM (web sources cross-checked across multiple independent publishers; codebase-specific claims are HIGH — read directly from this repo's own CLAUDE.md/PROJECT.md/memory)

## Critical Pitfalls

### Pitfall 1: Module-global state becomes a cross-tenant data/control channel

**What goes wrong:**
A variable meant to hold "the current state" for one user silently becomes shared across every subscriber. One customer's scan results, kill-switch state, or arming flag bleeds into another's — or worse, one customer's action (arming live trading, tripping a kill switch) affects everyone.

**Why it happens:**
This codebase is single-tenant today by construction: `scanner._state`, `scanner._health`, `scanner._task`, and an `lru_cache` on charge rates are module-level globals (confirmed in `memory/project-algo-bnf-vision.md`). `index_ai/risk_manager.py` runs one live-loss budget shared across India *and* crypto in the current single-user app — that's already cross-segment leakage by design, and the same pattern (one shared object, many logical owners) is exactly the shape that becomes cross-tenant leakage the moment a second subscriber exists. This is the single most predictable bug class in this specific migration: every module-global, `lru_cache`, and process-wide singleton in `index_ai/` and `crypto/` was written assuming exactly one user.

**How to avoid:**
Grep every module for global mutable state (`_state`, `_health`, `_task`, module-level dicts/caches, `@lru_cache` without tenant key) before writing the tenant model, not after. Thread `tenant_id` through every one of these paths as a required parameter, never an implicit ambient value. Per the project's own agreed shape, prefer **process isolation per tenant** (an isolated worker container per paying customer) over trying to retrofit every global into a tenant-keyed dict — a shared-process multi-tenant retrofit of code this global-heavy is a much larger and more error-prone rewrite than standing up one process per customer.

**Warning signs:**
Any function that reads a variable it didn't receive as a parameter. Any `@lru_cache` on a function whose result should differ per customer (charge rates are safe to share; positions/orders/credentials are not). Two customers' Telegram notifications or trade logs interleaving.

**Phase to address:**
The tenant-isolation/worker-architecture phase — must land before any second real customer is onboarded, and should be verified with an explicit two-tenant integration test (see Pitfall 9 test) before that phase is called done.

---

### Pitfall 2: Broker credentials stored or handled the way `.env` handles them today

**What goes wrong:**
Each subscriber's Dhan/Delta API key and secret get stored in a way that's readable by anyone with filesystem or DB access, logged in plaintext, or (worst case) one tenant's key gets used to place an order for another tenant because a lookup used the wrong id.

**Why it happens:**
The current app's own known debt: "`.env` secrets are plaintext on disk" (`.planning/codebase/CONCERNS.md`, referenced in PROJECT.md). That's an acceptable shortcut for one operator's own account; it is not an acceptable pattern to carry forward per-subscriber. Multi-tenant credential handling has a well-documented shape that this project has already scoped correctly on paper (Key Decisions: AWS Secrets Manager, not self-hosted Bitwarden) but has not built yet.

**How to avoid:**
Centralized encrypted secrets store (AWS Secrets Manager, matching the already-chosen AWS hosting decision) as source of truth, not env vars or a plain DB column. Encrypt each tenant's credentials with a key distinct from other tenants' (a compromised key/leak for one customer must not expose another's). Scope every credential lookup by `tenant_id` at the data-access layer, not just in application logic — the lookup function itself should make "give me tenant A's key while acting for tenant B" structurally impossible (pass the authenticated tenant context in, don't let a caller pick an arbitrary customer id). Never log a credential value, even at DEBUG. Rotate-on-revoke: if a subscriber disconnects their broker or cancels, the stored key must be invalidated immediately, not just soft-deleted.

**Warning signs:**
Any code path where a credential is fetched by an id passed in a request body/query param rather than derived from the authenticated session. Credentials appearing in `memory/server.log`-style logs (the project already fixed one plaintext-token-in-log incident for the Telegram bot token — the same class of bug is highly likely to recur with broker keys unless explicitly checked for).

**Phase to address:**
Credential vault phase, before the first non-Richard subscriber connects a real broker account. Verification: attempt (in a test) to fetch tenant B's credential using tenant A's authenticated context and confirm it's rejected.

---

### Pitfall 3: A subscriber's risk settings can't actually break the shared risk system, or can break someone else's

**What goes wrong:**
Two failure directions, both real: (a) a subscriber sets an aggressive lot size / risk cap, and because the risk gates are still keyed to shared/global state, their loss trips a kill switch that also halts other subscribers' trading; or (b) each subscriber's risk config is fully isolated but the *sizing itself* has no platform-side sanity ceiling, so a subscriber (or a bug in the sizing UI) can configure a lot size their account/margin can't support, producing rejected orders, partial fills, or a margin call the platform never modeled.

**Why it happens:**
The existing kill-switch design is explicitly cross-segment by intent today — "one live-loss budget across India + crypto" (`index_ai/risk_manager.py`, per CLAUDE.md) with `TIGHTENED`/`STOPPED` states that trip both India and crypto kill switches together. That's a deliberate, sensible design for one operator running multiple asset classes. It is the wrong shape the moment "India" and "crypto" become "subscriber A" and "subscriber B." Separately, subscriber-configurable position sizing is new surface area this app has never had — today only Richard sets lot counts and margin caps, with his own judgment as the implicit sanity check.

**How to avoid:**
Risk budgets, kill switches, and loss caps must be keyed per tenant (and the existing "tighten under stress rather than fully halt" philosophy — see `memory/risk-gates-tighten-not-stop.md` — should carry forward per-tenant, not abandoned). Enforce a platform-side hard ceiling on subscriber-set position size independent of what the subscriber types in (e.g., a max % of their own last-known account margin, refreshed from the broker, not just a client-side form validator) — the read-modify-write lesson already learned once in this codebase ("the client should send an absolute value, not a delta," per CLAUDE.md) applies directly to concurrent risk-setting updates too: two rapid setting changes from the same subscriber (or a stale dashboard tab) must not race and leave a lot size larger than either individual change intended.

**Warning signs:**
Any risk-state object keyed by asset class or strategy but not by tenant. A settings-update endpoint that accepts a new lot count without re-checking it against a freshly fetched account margin figure. Kill-switch trip events in logs that don't carry a tenant id.

**Phase to address:**
Per-tenant risk-manager rework phase, sequenced right after (or together with) tenant isolation — a kill switch that still spans tenants is a functional regression even if credentials and data are otherwise correctly isolated.

---

### Pitfall 4: Async handlers doing blocking I/O — a proven incident class here, and worse under multi-tenant load

**What goes wrong:**
A synchronous SQLite write, `.env`/config write, or blocking broker HTTP call inside an `async def` route handler stalls the entire event loop, freezing every concurrent request — not just the slow one.

**Why it happens:**
This has already happened twice in this exact codebase (CLAUDE.md: "Never put blocking I/O in an `async def` handler ... This has bitten twice"). Under single-tenant use, a stall is annoying (the dashboard poll hangs for one user: Richard). Under multi-tenant use, one subscriber's SQLite write (or one worker process serving multiple requests) stalls every other subscriber sharing that event loop at the same moment — the blast radius of the identical bug class grows with tenant count.

**How to avoid:**
Any blocking call in a handler goes through `asyncio.to_thread` — the pattern already adopted; the risk is regression as new multi-tenant endpoints get added quickly. Add this as an explicit code-review checklist item and, if feasible, a lint/CI check (grep for synchronous `sqlite3`/`open(...)`/`requests.` calls inside `async def` functions) so it's caught mechanically rather than relying on memory of two past incidents.

**Warning signs:**
Dashboard latency spikes that correlate with any single subscriber's write-heavy action (settings save, trade journal write). A worker process appearing to "hang" under concurrent load in a way that scales with request volume, not CPU.

**Phase to address:**
Should be a standing CI/lint gate introduced whenever the multi-tenant worker/API layer is built — not a one-time fix, since the underlying handler pattern will be touched a lot during that phase.

---

### Pitfall 5: A bug or bad strategy signal fires identically for every tenant at once (correlated failure, not independent)

**What goes wrong:**
Because every subscriber runs the *same* strategies against the *same* market data (NIFTY/BANKNIFTY/SENSEX signals, the same crypto strategy roster), a single bad signal, a broken indicator calculation, or a mis-tuned parameter doesn't cost one customer money — it costs every subscriber who has that (strategy, instrument) armed, simultaneously, in the same direction, at the same time. This is qualitatively different from most multi-tenant SaaS bugs (which tend to be isolated per tenant) and closer to Knight Capital's shape: one bad deploy, correlated loss across the whole customer base in minutes.

**Why it happens:**
Strategy logic, signal computation, and risk gating are shared code paths by design (that's the product — "run proven algo strategies without building them themselves," per PROJECT.md's Business Context). Shared code means shared bugs. A regression in `plan_builder`, `strategy_router`, or a crypto strategy module isn't tenant-scoped; it fires wherever that strategy is armed.

**How to avoid:**
Staged/canary rollout for any strategy code change that affects live order placement — never push a strategy-logic change to 100% of armed subscribers simultaneously; ship to a small cohort (or paper-only) first, per the project's own existing readiness-bar discipline (30+ trades, 14+ days, net-positive) extended to *code changes*, not just new strategies. A single platform-wide kill switch reachable in one action (already a pattern here — per-tenant kill switches from Pitfall 3 don't replace the need for an operator-level "halt everything" switch for exactly this correlated-failure case). Feature-flag strategy versions per tenant so a bad update can be rolled back for affected tenants without a full redeploy.

**Warning signs:**
Multiple subscribers' loss events clustering in the same few minutes on the same (strategy, instrument). A strategy-code deploy immediately followed by a spike in kill-switch trips across tenants.

**Phase to address:**
Operational/monitoring phase, and should inform the deployment process for the multi-tenant worker phase (canary/staged rollout is an architecture decision, not an afterthought).

---

### Pitfall 6: Treating "each subscriber connects their own broker" as sufficient SEBI compliance on its own

**What goes wrong:**
The team assumes that because each subscriber trades through their own Dhan/Delta account (not a pooled account), the platform is automatically clear of SEBI's algo-trading and investment-advice rules. It is not — distributing/operating strategies for other people, and the broker-integration mechanics themselves, both carry specific obligations independent of whose money moves.

**Why it happens:**
"Each subscriber owns their broker relationship" solves the *pooled-fund custody* problem, which is a real and important risk to have designed away — but it's a different problem from *registration and disclosure* requirements, which attach to the act of providing/operating the algo for someone else, not to who custodies the funds.

**What SEBI actually requires (as of this research, Sept 2026 — verify current status before build, rules were still rolling out through Apr 2026):**
- SEBI's Feb 2025 retail algo framework (Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013) requires every registered retail algo to carry a unique Algo ID, exchange-tagged order IDs for audit, and — since Oct 2025 broker-side rollout, mandatory from 1 April 2026 — that all automated orders route through a SEBI-registered broker's pre-approved infrastructure with a unique Strategy ID, static/fixed-IP whitelisting, OAuth login, 2FA, and automatic session logout before each pre-open. An Orders-Per-Second threshold of 10 distinguishes plain API use from algo trading needing separate exchange registration.
- Separately: **anyone selling, distributing, or operating algo strategies for other people must register as a SEBI Research Analyst** — this is squarely what a subscription algo-trading product does. An undisclosed ("black box") strategy still requires this registration; disclosing the logic doesn't exempt you.
- **Brokers are treated as principals responsible for every algo running on their platform** — meaning Dhan/Delta's own compliance posture toward this app matters, not just this app's posture toward SEBI. A broker integration that looks like "mass-distributed algo software" (per SEBI's explicit warnings about plug-and-play bots and influencer-sold algo software) is a targeted enforcement pattern, and this project's stated destination — subscription distribution of proven strategies to retail traders — matches that pattern closely enough to need counsel, not a self-assessment.

**How to avoid:**
Get SEBI-specific legal counsel before the first paying, non-Richard subscriber goes live with real money — this is already flagged in PROJECT.md as "a real constraint on the business model... to be raised whenever distribution work is planned," and this research reinforces that it's not a formality: RA registration, per-strategy exchange IDs, static-IP compliance (which the multi-tenant architecture's "one Elastic IP" decision already anticipates correctly), and the April 2026 broker-API deadline are concrete, dated requirements, not vague future risk. Build the static-IP and Strategy-ID plumbing into the broker-integration layer from the start rather than retrofitting it once a subscriber is already live.

**Warning signs:**
Any plan to onboard a paying subscriber's real-money trading before RA registration (or an equivalent counsel-cleared structure) is settled. Broker integration code with no concept of a per-strategy exchange-assigned ID.

**Phase to address:**
Must be resolved (counsel engaged, registration path chosen) before the "arm real subscriber money" milestone — treat as a gating dependency on the go-live phase, not a parallel workstream that can lag behind engineering.

---

### Pitfall 7: Monitoring built for one operator's judgment doesn't scale to catching a losing strategy across many tenants

**What goes wrong:**
Today, Richard personally watches the dashboard and would notice "this is losing too much, too fast." At subscriber scale, nobody is watching every tenant's live P&L in real time, so a strategy that's quietly losing money for a subset of subscribers (a particular instrument, a particular risk-setting combination) can run for days before anyone notices — the exact failure mode automated kill switches exist to prevent, except the switches are currently tuned to one operator's risk tolerance, not to "alert a human that something looks wrong across the fleet."

**Why it happens:**
The project's existing safety net — per-(strategy, instrument) scorecards, live-journal-only ML, Telegram trade notifications — was all built and tuned for a single dashboard that one person checks. None of it currently answers "across all subscribers, is any strategy/instrument combination behaving worse than its historical readiness bar, right now" as an alert rather than something you'd have to go look up.

**How to avoid:**
Add fleet-level monitoring distinct from per-tenant dashboards: aggregate, near-real-time alerting when a (strategy, instrument) combination's live win rate or net P&L across all subscribers currently running it drops meaningfully below its established readiness-bar baseline (the scorecard math already exists — this is "run it across tenants and alert on regression," not new math). Alert on kill-switch trip *rate* (e.g., >X% of subscribers running a strategy tripped their kill switch in the last hour) as a distinct, louder signal than any single trip. Make sure Telegram (or an equivalent) notifies an operator, not just the affected subscriber, for anomalies — a subscriber losing money on their own settings is expected risk they accepted; the same pattern appearing across many subscribers simultaneously is a platform bug.

**Warning signs:**
No dashboard view answers "which strategies are underperforming their own historical baseline right now, across all customers" without a manual query. Kill-switch trips logged but not aggregated/alerted on.

**Phase to address:**
Operational monitoring phase, ideally landing alongside or shortly after the per-tenant risk-manager rework (Pitfall 3) — the alerting needs per-tenant kill-switch events to already exist and be structured before it can aggregate them.

---

### Pitfall 8: Auth built as an afterthought stopgap stays the permanent auth

**What goes wrong:**
The current `ADMIN_API_SECRET` gate is explicitly "off by default, not yet the full per-user auth the multi-tenant destination needs" (PROJECT.md). The risk in this exact migration is that a version of this stopgap — single shared secret, optional, protecting only "the five endpoints that can arm live orders" — gets extended rather than replaced, because it already half-works and full per-subscriber auth (login, session, authorization-per-tenant-resource) is a bigger lift.

**Why it happens:**
It's the path of least resistance: the stopgap exists, adding one more secret or one more protected endpoint feels incremental, and a full auth system (user accounts, sessions, per-tenant authorization checks on every data-access path) is genuinely a large piece of work that's tempting to defer.

**How to avoid:**
Treat "real per-subscriber authentication and authorization" as a hard, non-negotiable prerequisite phase for onboarding any second real customer — not something layered on top of the admin-secret pattern. Every endpoint needs to check not just "is this caller authenticated" but "is this caller authorized for *this specific tenant's* resource" (the authorization check, not just authentication, is where cross-tenant bugs live — see Pitfall 9). Budget this as its own phase with its own verification, not a checkbox inside the broker-integration or dashboard-split phases.

**Warning signs:**
Any endpoint added during the multi-tenant build that's protected by "does the request have a valid secret" rather than "does the authenticated subscriber own the resource being accessed."

**Phase to address:**
Its own phase, sequenced early (auth/authorization is a dependency for almost every other multi-tenant phase — data isolation, credential vault access, risk settings, dashboard split all need "who is asking" answered first).

---

### Pitfall 9: No test actually proves tenant isolation — it's assumed, not verified

**What goes wrong:**
Every individual piece (credential vault, risk manager, database schema) gets built with tenant-scoping in mind, but nothing end-to-end verifies that tenant A genuinely cannot see, affect, or trade using tenant B's anything. The bug that ships is usually not in the obviously-shared code — it's in the one query, cache key, or background job that was missed.

**Why it happens:**
Tenant isolation is a property of the *whole system*, not any single module, and it's easy to review each piece in isolation and conclude it's fine while a gap exists in how two pieces compose (e.g., the credential vault is correctly tenant-scoped, but a background job that reads from it iterates without re-checking tenant context).

**How to avoid:**
Build a standing two-tenant integration test fixture as part of the tenant-isolation phase itself: spin up two fake tenants with distinct credentials/settings/risk state, and assert that tenant A's authenticated session can never read, modify, or trigger an order using tenant B's identifiers — run this test on every PR that touches auth, data access, credentials, or risk state, not just once. This is the single highest-leverage test in the whole migration given the stakes (real broker orders, real subscriber money).

**Warning signs:**
"We're pretty sure isolation is fine" without a runnable test backing that statement. Code review catching an isolation bug that a mechanical test would have caught for free on every future change.

**Phase to address:**
Introduce the fixture in the tenant-isolation phase; keep it running as a permanent CI gate for every subsequent phase that touches shared infrastructure.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|--------------------|-----------------|------------------|
| Shared risk budget/kill switch across asset classes (current design) | Simple, matches one operator's mental model | Becomes cross-tenant leakage the moment a second real customer exists | Never, once a second subscriber trades real money — must be per-tenant before go-live |
| Extending `ADMIN_API_SECRET` instead of building real per-subscriber auth | Fast, reuses existing pattern | Every new "protected" endpoint inherits an all-or-nothing secret model, not per-tenant authorization | Only as a bridge for Richard's own admin access, never for subscriber-facing endpoints |
| Plaintext `.env`-style credential storage carried into multi-tenant | Zero new infra | One breach exposes every subscriber's broker keys at once | Never for subscriber credentials — acceptable only for the operator's own single account, as today |
| Deploying a strategy-logic change to all armed subscribers at once | Simpler release process | Turns any regression into a correlated, fleet-wide loss event (Pitfall 5) | Only after canary/staged rollout tooling exists; never for the initial multi-tenant build |
| Skipping the two-tenant isolation test because "the code looks right" | Saves setup time | The exact bug class most likely to cause real financial and reputational damage ships unnoticed | Never |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| Dhan API (per-subscriber) | Reusing one process-wide Dhan session/rate-limit budget across subscribers, causing one subscriber's volume to throttle another's orders | Rate-limit and session-manage per subscriber's own Dhan credentials, independently |
| Delta Exchange India API | Same rate-limit/session-sharing mistake as Dhan, plus assuming the INR↔USD conversion logic (already modeled per-account today) stays correct when multiple accounts convert concurrently | Verify the FX conversion path is stateless/per-request, not cached against a single shared rate object |
| AWS Secrets Manager | Treating it as "just another config source" fetched once at boot, missing rotation/revocation for a subscriber who disconnects | Fetch per-request or with short-lived cache keyed by tenant + credential version; invalidate on disconnect |
| Static/Elastic IP + broker IP whitelisting (SEBI requirement) | Building this as a manual per-customer instruction ("go whitelist this IP") with no verification that it actually happened before allowing live arming | Gate live-arming on a confirmed successful authenticated call through the whitelisted IP, not just a setup-instructions page |
| Telegram notifications (per-subscriber) | Reusing one bot/chat config, so subscriber notifications leak to the operator's chat or each other's | Per-subscriber bot token/chat id, or a per-tenant routing layer if a shared bot is used |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Authorization checked at the UI/route level only, not the data-access layer | A correctly-gated dashboard page can still leak data via a raw query that forgot the tenant filter | Enforce tenant scoping as close to the database/data-access layer as possible, not just in route handlers |
| One shared IP allowlisted per the SEBI static-IP requirement without per-tenant broker-side verification that whitelisting was actually completed | A subscriber's orders silently fail (or worse, an incorrectly-set-up whitelist lets an unintended path through) | Verify each subscriber's broker-side IP whitelist with a live test call before allowing them to arm live trading |
| Credential vault access logged only at "vault was accessed," not "by which tenant-context, for which tenant's secret" | Can't detect or investigate a cross-tenant credential access after the fact | Audit log every secret fetch with both the requesting tenant/session and the target tenant/credential id |
| Kill-switch / arming state kept in a shared cache or global without a tenant key (extension of Pitfall 1) | One subscriber's emergency stop doesn't stop the right account, or stops everyone's | Tenant-scope every piece of trading-control state, verified by the Pitfall 9 test |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-------------------|
| Subscriber sets a position size the platform silently caps or rejects without clear feedback | Subscriber thinks their risk setting is active when it isn't, and is surprised by smaller/no fills | Always echo back the effective, enforced setting after any risk-config change, not just accept the input |
| No clear distinction in the dashboard between "your account's kill switch tripped" and "the platform paused this strategy for everyone" | Subscriber can't tell if it's their own risk event or a platform-wide issue, and support gets confused escalations | Separate, clearly labeled status indicators for tenant-level vs. platform-level trading halts |
| Setup requires the subscriber to hand-edit anything (explicitly ruled out already in PROJECT.md: "Requiring a customer to hand-edit `.env` is not an acceptable setup path for any feature") | Breaks the subscription product's basic usability promise | Every subscriber-facing setting goes through the app's own settings UI/API, never a file edit |

## "Looks Done But Isn't" Checklist

- [ ] **Tenant data isolation:** Often missing coverage for background jobs/schedulers and cache layers (not just request-handling code) — verify with the two-tenant integration test (Pitfall 9), not code review alone.
- [ ] **Credential vault:** Often missing rotation-on-disconnect and audit logging of *which tenant* accessed *whose* secret — verify by disconnecting a test subscriber's broker and confirming the stored credential is immediately unusable.
- [ ] **Per-tenant risk/kill-switch:** Often still shares state across a "segment" boundary (India/crypto/commodities) the way the current single-tenant app intentionally does — verify by tripping one test tenant's kill switch and confirming a second test tenant's trading is unaffected.
- [ ] **SEBI compliance surface:** Often assumed satisfied by "each subscriber uses their own broker account" alone — verify RA-registration status, per-strategy exchange Strategy IDs, and static-IP whitelisting are each explicitly confirmed with counsel/broker, not inferred.
- [ ] **Monitoring/alerting:** Often built as per-tenant dashboards only, with no fleet-level aggregation — verify there's an alert that fires when a (strategy, instrument) regresses across multiple subscribers at once, not just a page a human could check.
- [ ] **Blocking I/O in async handlers:** Often reintroduced by new code during a fast-moving multi-tenant build even though it's already bitten this project twice — verify with a grep/lint check in CI, not memory.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|-----------------|-----------------|
| Cross-tenant data leakage discovered post-launch | HIGH | Immediately revoke/rotate all affected tenants' broker credentials, halt live trading platform-wide pending root cause, disclose to affected subscribers, patch, re-run the two-tenant isolation test before re-enabling |
| Correlated strategy bug fires across many tenants simultaneously | HIGH | Trip the platform-wide kill switch immediately, roll back the strategy-code change, reconcile each affected subscriber's actual fills against expected behavior before resuming that strategy for anyone |
| A subscriber's risk setting produced an unintended large position | MEDIUM | Manual override/close (already a standing pattern in this codebase per `memory/manual-override-controls.md`) to flatten the position, then add the platform-side sanity ceiling that should have prevented it |
| SEBI compliance gap discovered after subscribers are live (e.g., RA registration incomplete) | HIGH | Pause new subscriber onboarding and any strategy-distribution activity that requires RA registration, engage counsel immediately, existing subscribers' own-account trading may be able to continue depending on counsel's read — do not guess, get the legal read before deciding |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|--------------------|----------------|
| Module-global state → cross-tenant leakage (1) | Tenant isolation / worker architecture phase | Two-tenant integration test (Pitfall 9's fixture) passes on every module touched |
| Credential storage/handling (2) | Credential vault phase | Attempted cross-tenant credential fetch is rejected; access audit log shows tenant context on every fetch |
| Shared risk budget / kill switch (3) | Per-tenant risk-manager rework phase | Tripping test tenant A's kill switch leaves test tenant B trading normally |
| Blocking I/O in async handlers (4) | Multi-tenant API/worker layer phase | CI lint/grep check for sync I/O inside `async def`; no regression of the two prior incidents |
| Correlated fleet-wide strategy bug (5) | Deployment/rollout process, set up during the worker-architecture phase | Canary rollout used for every live-affecting strategy change; platform-wide kill switch reachable in one action |
| SEBI registration/compliance gaps (6) | Go-live/compliance gating phase (blocks onboarding real non-Richard subscribers) | Counsel sign-off on RA registration path, Strategy ID plumbing verified against broker, static-IP whitelist confirmed live per subscriber |
| Monitoring built for one operator, not a fleet (7) | Operational monitoring phase (after per-tenant risk-manager rework) | Alert fires in a test when a (strategy, instrument) regression is simulated across multiple test tenants |
| Auth stopgap extended instead of replaced (8) | Dedicated auth/authorization phase, sequenced early | Every endpoint requires per-tenant-resource authorization, not just a shared secret; admin-secret pattern retired for subscriber-facing paths |
| No end-to-end isolation test (9) | Tenant isolation phase (test fixture built alongside the isolation work itself) | Fixture runs in CI on every PR touching auth, data access, credentials, or risk state |

## Sources

- [SEBI Algo Trading Rules 2025: What Indian Traders Must Know — Motilal Oswal](https://www.motilaloswal.com/learning-centre/2025/6/sebi-regulations-on-algorithmic-trading-in-india)
- [SEBI Algo Trading Rules 2026: Complete Guide for Retail Traders — HDFC Sky](https://hdfcsky.com/sky-learn/algo-trading/sebi-algo-trading-rules)
- [SEBI Algo Trading Regulations 2026: A Guide for Retail Investors — Liquide](https://blog.liquide.life/sebi-algo-trading-regulations-2026/)
- [Is SEBI Banning Algo Trading in India in 2026? — Algotest](https://algotest.in/blog/is-sebi-banning-algo-trading-in-india/)
- [SEBI Algo Trading Rules and Regulations in India — Fyers](https://fyers.in/blog/sebi-algo-trading-rules-and-regulations-in-india/)
- [Automate SEBI Registered Analyst Strategies — Algotest](https://algotest.in/blog/sebi-registered-analyst-strategies/)
- [Regulating Online Share Trading and Investment Platforms in India — LegalServiceIndia](https://www.legalserviceindia.com/Legal-Articles/regulating-online-share-trading-and-investment-platforms-in-india-a-sebi-led-framework/)
- [API Security for SaaS: Protect Multi-Tenant Apps & Data — Indusface](https://www.indusface.com/blog/api-security-for-saas-platforms/)
- [API Key Management Best Practices for Secure Secrets Storage — GitGuardian](https://blog.gitguardian.com/secrets-api-management/)
- [API Key Management Best Practices for Multi-Tenant Apps — Corsair](https://corsair.dev/blog/api-key-management-best-practices-multi-tenant-apps)
- [Tenant-Aware Caching Bugs — Sourcery](https://www.sourcery.ai/security/categories/tenant_aware_caching_bugs)
- [Multi-Tenant Leakage: When "Row-Level Security" Fails in SaaS — Medium/InstaTunnel](https://medium.com/@instatunnel/multi-tenant-leakage-when-row-level-security-fails-in-saas-da25f40c788c)
- [Multi Tenant Security Cheat Sheet — OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)
- [Knight Capital SMARS algorithmic trading incident of August 2012 — Postmortems.app](https://postmortems.app/postmortem/ef3cc3e0-c51c-4f5d-8dd0-cc8a8dfdcbb2)
- [Lessons from Algo Trading Failures — LuxAlgo](https://www.luxalgo.com/blog/lessons-from-algo-trading-failures/)
- This project's own codebase memory: `CLAUDE.md` (project instructions — money-path modules, blocking-I/O incident history, two-lock arming), `PROJECT.md`, `memory/project-algo-bnf-vision.md`, `memory/risk-gates-tighten-not-stop.md`, `memory/manual-override-controls.md`

---
*Pitfalls research for: Multi-tenant algo-trading SaaS (QuantHawk / Algo BNF)*
*Researched: 2026-09-30*
