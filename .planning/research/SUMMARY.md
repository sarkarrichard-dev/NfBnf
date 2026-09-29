# Project Research Summary

**Project:** QuantHawk / Algo BNF — subscription distribution milestone
**Domain:** Multi-tenant fintech SaaS (subscriber-facing algo-trading platform, India options/futures via Dhan + crypto perps via Delta Exchange India)
**Researched:** 2026-09-30
**Confidence:** MEDIUM

## Executive Summary

This is not a new product — it's converting a working, single-operator, real-money trading app (~14k LOC FastAPI + React, 660 tests, already trading NIFTY/BANKNIFTY/SENSEX and crypto perps) into a subscription SaaS where strangers connect their own broker accounts. Every piece of research converges on the same shape QuantHawk had already tentatively chosen: **silo the money path, pool everything else** (AWS's "bridge model") — one isolated worker container per paying subscriber running the existing trading engine near-unchanged, plus a small shared control plane for auth/billing/tenant registry, all workers egressing one static IP that each subscriber whitelists on their own broker account. Don't second-guess that architecture; the job is filling in what it doesn't yet specify (interim data layer, exact tenant-boundary checkpoint, migration order) and building it in the right sequence.

The recommended approach is deliberately conservative about the trading logic and aggressive about isolation: extract module-global state (scanner._state, the shared risk-manager kill switch, lru_caches) into per-tenant parameters first — this is the actual first blocker, needed at subscriber #2, not #100 — then containerize, keeping SQLite-per-tenant-container as an honest interim step rather than forcing a Postgres cutover before it's needed (Postgres/RLS is only required for genuinely cross-tenant queries: the control plane, admin rollups, billing). Credential storage (AWS Secrets Manager, one path per tenant, IAM-scoped), auth (Cognito, chosen for AWS-native + budget fit over Clerk/Auth0), and per-tenant risk/kill-switch state are the three concrete build blocks layered on top.

The two biggest risks are not technical in the usual SaaS sense. First, correlated failure: every subscriber runs the same shared strategy code against the same market data, so a bad deploy or mis-tuned parameter doesn't cost one tenant, it costs everyone armed on that (strategy, instrument) simultaneously — Knight-Capital-shaped, not typical-SaaS-shaped. Mitigate with canary/staged strategy rollout and a one-action platform-wide kill switch, both fully separate from per-tenant kill switches. Second, SEBI regulatory exposure: "each subscriber connects their own broker account" solves the custody problem but not the registration problem — operating/distributing algo strategies for other people almost certainly triggers Research Analyst registration regardless of custody, and an April 2026 broker-infrastructure deadline (static IP, per-strategy exchange IDs, OAuth+2FA, daily re-auth) is a hard, dated gate, not background risk. Both need resolution (counsel for the regulatory piece, canary tooling for the correlated-failure piece) before the first non-Richard subscriber trades real money — this is a go-live gate, not a nice-to-have.

## Key Findings

### Recommended Stack

The stack changes are narrowly scoped to the four multi-tenancy blockers (data isolation, credential vault, auth split, worker isolation) — nothing about the existing trading logic, dashboard framework, or brokers changes.

**Core technologies:**
- **PostgreSQL (RDS/Aurora) with Row-Level Security** — for the control plane only (tenants, auth, billing, audit log); replaces nothing in the per-tenant worker, which keeps SQLite (see Architecture below)
- **SQLAlchemy 2.0 async + asyncpg** — ORM/driver for the control plane, avoids reintroducing the "blocking I/O in async handler" bug class already hit twice in this codebase
- **AWS Secrets Manager** (already decided in PROJECT.md, confirmed correct here) — one secret path per tenant per broker (tenant/{id}/dhan), IAM-scoped so a worker's task role reads only its own tenant's path; use per-tenant KMS CMKs, not the default key
- **Amazon ECS on AWS Fargate** (already decided, confirmed) — one isolated worker container per paying subscriber; each Fargate task is its own micro-VM, no lateral movement between subscribers
- **AWS Cognito** (new recommendation over Clerk/Auth0) — cheapest at scale, AWS-native, integrates with IAM to scope a subscriber's identity to their own worker/secret; rougher DX than Clerk is the tradeoff
- **AWS Step Functions** (new recommendation) — for the multi-step tenant lifecycle (onboard, validate broker keys, provision worker, arm), not for the always-on trading loop itself and not via Celery/Temporal

### Expected Features

**Must have (table stakes) — P1, first paying outside subscriber:**
- Per-subscriber broker connection with identity verification at connect time (reuses the referral-fraud verification mechanism already designed)
- Per-subscriber isolated worker with its own risk state, kill switch, position sizing
- Paper/live toggle reusing the existing two-lock + exact-phrase arming pattern
- Subscriber-visible live P&L / trade history (extension of the existing scorecard)
- Basic subscription billing (one plan, recurring, cancel — India needs Razorpay/Stripe-India, not a US-only processor)
- Static-IP-compliant architecture (already decided, this is a legal gate)
- White-box disclosure of what each strategy does in plain terms

**Should have (differentiators) — P2, mostly free reuse of existing internal discipline:**
- Real, verified live-journal track record per strategy — surfacing QuantHawk's existing "never trust an unmeasured number" discipline publicly is a genuine trust edge over competitors (Streak, Tradetron mostly show self-reported/backtested numbers)
- Per-(strategy, instrument) transparency instead of one blended equity curve
- Readiness-gated rollout shown to subscribers, honest "we killed this, here's why" reporting
- Tightens-not-halts risk response made visible, not just internal

**Defer (v2+):**
- Multi-broker choice per segment — deliberately sequenced after credential vault/multi-tenancy per the existing Broker Adapter Plan
- Comparative/leaderboard views — anti-feature unless anchored to the same readiness-gated numbers already used internally
- Exchange Algo ID registration pipeline — becomes P1 the moment order volume nears the exemption threshold

**Anti-features flagged:** pooled-account trading (heavier SEBI burden, rejected already), undisclosed "black box" strategy marketing (triggers RA registration regardless), ML auto-scaling subscriber position size without explicit cap (conflicts with existing ML guardrails), raw returns leaderboards, credential-sharing "one-click connect all brokers" shortcuts.

### Architecture Approach

Silo the money path, pool the rest (AWS "bridge model"): a small control plane (auth, billing, tenant registry, shared dashboard shell) sits on shared Postgres and never holds a broker credential or executes a trade; each tenant gets its own worker container running today's index_ai app almost unmodified, with its own SQLite journal and its own Secrets-Manager-scoped credential, all egressing through one shared NAT Gateway/Elastic IP. index_ai/ stays the worker image (plumbing changes — config source, DB path, credential source — not trading-logic rewrites); control_plane/ is a new, intentionally small service; the dashboard stays one codebase scoped by role/auth, not forked into separate admin/subscriber apps.

**Major components:**
1. Control plane (new) — signup, auth/session, billing state, tenant registry, dashboard proxy; the only place tenant identity is checked when crossing from shared to tenant-specific resources
2. Per-tenant worker (existing code, containerized) — runs today's scan-plan-execute loop against one tenant's broker account and risk budget, parameterized by tenant config instead of a shared .env
3. Secrets/credential vault — AWS Secrets Manager, one path per tenant per broker, IAM-scoped per worker task role
4. Shared egress — one NAT Gateway/Elastic IP every tenant whitelists once
5. Shared dashboard — one React/Vite build, tenant-scoped by auth token, admin as one role among several

Migration order matters: extract module-global state to per-instance first (Pattern 4 — this alone is what lets two copies of unmodified code run safely side by side), keep SQLite-per-tenant-container as the interim data layer (Postgres is only needed for genuinely cross-tenant queries — control plane, billing, admin rollups), and only then does "one container per tenant" become a deployment decision rather than a rewrite.

### Critical Pitfalls

1. Module-global state becomes a cross-tenant channel — scanner._state, the shared India+crypto risk budget, lru_caches were all written assuming one user; thread tenant_id through everything or (preferred) isolate by process so retrofitting every global isn't necessary. Verify with a mandatory two-tenant integration test, not code review.
2. Broker credentials handled the way .env handles them today — plaintext-on-disk is fine for one operator, not for subscribers; Secrets Manager with per-tenant keys, credential lookups scoped by authenticated tenant context (never a caller-supplied id), audit-logged, rotate-on-disconnect.
3. A subscriber's risk settings break the shared risk system, or vice versa — today's "one live-loss budget across India + crypto" is deliberate for one operator and wrong the moment those become "subscriber A" and "subscriber B"; risk budgets and kill switches must be keyed per tenant, with a platform-side hard ceiling on subscriber-set position size.
4. Correlated fleet-wide strategy bug — shared strategy code means a bad deploy fires identically for every armed subscriber at once (Knight-Capital-shaped); requires canary/staged rollout for any live-affecting strategy change plus a one-action platform-wide kill switch, distinct from per-tenant kill switches.
5. Treating "subscriber owns their broker account" as sufficient SEBI compliance — it solves custody, not registration; providing/operating algo strategies for other people likely triggers Research Analyst registration regardless, and an April 2026 broker-infrastructure deadline (static IP, Strategy IDs, OAuth+2FA) is concrete and dated. Get counsel before the first non-Richard subscriber trades real money.
6. No test actually proves tenant isolation — build a standing two-tenant integration test fixture (distinct fake tenants, assert A can never read/modify/trigger an order via B's identifiers) as part of the isolation phase itself, run it in CI on every PR touching auth/data/credentials/risk.

## Implications for Roadmap

### Phase 1: Tenant-Isolation Foundation (state extraction + two-tenant test fixture)
**Rationale:** Every other subscriber-facing feature (broker connection, kill switches, billing, P&L, referrals) sits on top of "per-subscriber state exists," which today it does not. This is the dependency root for the entire milestone.
**Delivers:** Module-global state (scanner._state, risk-manager budget, caches) converted to per-instance/per-tenant parameters; the two-tenant integration test fixture running in CI.
**Addresses:** Foundation for all P1 features in FEATURES.md.
**Avoids:** Pitfall 1 (cross-tenant state leakage), Pitfall 9 (unverified isolation).

### Phase 2: Auth & Authorization
**Rationale:** Nearly every subsequent phase (credential vault access, risk settings, dashboard split) needs "who is asking, and are they authorized for this specific tenant's resource" answered first. Must not be an extension of the existing ADMIN_API_SECRET stopgap.
**Delivers:** Per-subscriber login/session (Cognito), role-based access (subscriber vs. admin), tenant-scoped authorization checked at the data-access layer, not just routes.
**Uses:** AWS Cognito, JWT verification middleware (python-jose/PyJWT).
**Avoids:** Pitfall 8 (stopgap auth becoming permanent auth).

### Phase 3: Credential Vault
**Rationale:** Broker connection — the entire premise of the product — cannot ship until credentials can be stored per-subscriber, safely.
**Delivers:** AWS Secrets Manager integration, one path per tenant per broker, IAM-scoped reads, rotate-on-disconnect, audit logging of every fetch.
**Implements:** Architecture's Secrets/credential vault component.
**Avoids:** Pitfall 2 (credential mishandling).

### Phase 4: Per-Tenant Worker Containerization
**Rationale:** With state extracted (Phase 1), auth in place (Phase 2), and a vault to read from (Phase 3), the trading engine can now actually be deployed as one isolated container per subscriber without being rewritten.
**Delivers:** index_ai/ packaged as a worker image; one Fargate task per tenant; per-tenant SQLite journal; shared NAT Gateway/Elastic IP; per-tenant risk manager/kill switch (no more shared India+crypto budget).
**Uses:** ECS Fargate, shared Elastic IP, per-tenant SQLite (interim, not Postgres).
**Avoids:** Pitfall 3 (shared risk/kill switch), Pitfall 5 groundwork (canary rollout tooling should land here too).

### Phase 5: Control Plane (billing, tenant registry, provisioning)
**Rationale:** Once workers can be provisioned per tenant, the control plane that orchestrates signup-validate-provision-arm needs to exist.
**Delivers:** Small new control_plane/ service on Postgres, subscription billing (Razorpay/Stripe India), tenant registry, provisioning workflow.
**Uses:** Postgres + SQLAlchemy 2.0 async, AWS Step Functions for the onboarding workflow.

### Phase 6: Subscriber-Facing Dashboard Split
**Rationale:** Only meaningful once auth (Phase 2) and per-tenant data (Phase 4) exist to scope it.
**Delivers:** One dashboard codebase, role-scoped views (subscriber vs. admin), live P&L/trade history per subscriber, subscriber kill-switch control, static-IP whitelist verification flow.
**Addresses:** P1 features — subscriber-visible P&L, kill switch, position sizing controls.

### Phase 7: Compliance & Go-Live Gate
**Rationale:** Must gate the first non-Richard real-money subscriber, not run in parallel and lag behind engineering.
**Delivers:** SEBI counsel engagement outcome, RA registration path decided, per-strategy exchange Strategy ID plumbing, verified static-IP whitelist confirmation before live arming, white-box strategy disclosure copy.
**Avoids:** Pitfall 6 (compliance assumed satisfied by broker-account-ownership alone).

### Phase 8 (parallel, ongoing from Phase 4): Fleet Monitoring & Canary Rollout
**Rationale:** Needs per-tenant kill-switch events (Phase 4) to exist before it can aggregate/alert on them; should inform the worker deployment process from the start rather than being bolted on later.
**Delivers:** Aggregate alerting when a (strategy, instrument) regresses across subscribers, kill-switch trip-rate alerting, staged/canary rollout tooling for strategy-code changes, platform-wide one-action kill switch.
**Avoids:** Pitfall 5 (correlated failure), Pitfall 7 (monitoring built for one operator, not a fleet).

### Phase Ordering Rationale

- State extraction and the isolation test fixture come first because every later phase's correctness depends on them, and retrofitting isolation after subscriber-facing features exist is far more expensive than building it in.
- Auth precedes the credential vault and worker containerization because "who is asking" is a prerequisite for both — you can't scope a secret or a container to a tenant without an authenticated tenant identity.
- Containerization (Phase 4) deliberately comes after state extraction, not before — Pattern 4 from ARCHITECTURE.md is explicit that container-per-tenant only becomes a safe deployment decision once the code no longer assumes it's the sole instance.
- Compliance is placed as a gate before go-live, not a phase with its own timeline slot, because it's dated (April 2026 broker deadline) and blocking, not merely important.
- Postgres is deliberately not introduced for the per-tenant trading journal in this sequence — only the control plane needs it — per Architecture Pattern 5's interim-SQLite recommendation, which avoids over-building before tenant count demands it.

### Research Flags

Phases likely needing deeper research during planning:
- Phase 7 (Compliance & Go-Live Gate): SEBI's actual circular text (not secondary summaries) needs a primary-source read or counsel input before the Strategy-ID/broker-infrastructure plumbing is finalized — PITFALLS.md flags one claim (algos may need broker-owned infrastructure) as single-source and unverified.
- Phase 5 (Control Plane billing): India-specific payment gateway integration (Razorpay vs. Stripe India) wasn't researched in depth here — needs its own pass.
- Phase 4 (Worker containerization): ECS Fargate task-definition/IAM-scoping specifics for per-tenant Secrets Manager access should be validated against current AWS docs before implementation, not just this research's summary.

Phases with standard patterns (skip research-phase):
- Phase 1 (Tenant-isolation foundation): Well-documented pattern (state extraction, two-tenant test fixture); this research already provides enough detail.
- Phase 2 (Auth): Cognito/JWT-middleware pattern is standard and well-documented.
- Phase 3 (Credential vault): AWS Secrets Manager usage pattern is standard, already decided in PROJECT.md and confirmed here.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM | Web-search cross-checked across multiple sources; no official vendor docs pulled via Context7 this pass — re-verify exact version pins before adding to requirements.txt |
| Features | MEDIUM | SEBI regulatory claims cross-checked across several secondary sources (MEDIUM-HIGH); competitor feature claims are blog-sourced (MEDIUM) |
| Architecture | MEDIUM | General multi-tenant SaaS/isolation patterns are well-documented and cross-checked (AWS whitepapers, Temporal docs); no public trading-bot SaaS publishes its internal worker-isolation architecture, so that specific inference is LOW; SEBI infra-hosting language is a single-pass web summary, not primary source |
| Pitfalls | MEDIUM overall, HIGH for codebase-specific claims | Codebase-specific pitfalls (module-global state, blocking-I/O history) are read directly from this repo's own CLAUDE.md/PROJECT.md/memory — HIGH confidence. Regulatory and general SaaS-security pitfalls are cross-checked secondary sources — MEDIUM |

**Overall confidence:** MEDIUM

### Gaps to Address

- SEBI circular primary-source verification: All four research files flag the same gap — secondary summaries agree on static-IP/April-2026/RA-registration themes, but the actual circular text (SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013) hasn't been read directly, and one claim (broker-owned-infrastructure-only hosting) appeared in only one non-primary source. Resolve with counsel before Phase 7, and before the static-IP architecture is treated as fully sufficient.
- Whether algos must run on broker-owned infrastructure: If true, this could materially conflict with the "own AWS worker per tenant" architecture — flagged as unverified, needs resolution early enough to not require an architecture rework late.
- Delta Exchange India's own IP-whitelisting requirements: Not confirmed to align with a single shared platform IP across many customers' individual Delta accounts — verify directly with Delta's API docs.
- Payment gateway choice for India (Razorpay vs. Stripe India): Not researched in this pass — needs a dedicated pass before Phase 5.
- Exact version pins for new dependencies (SQLAlchemy, asyncpg, Alembic, aws-secretsmanager-caching): read via search snippets, re-verify against PyPI directly before pinning.

## Sources

### Primary (HIGH confidence)
- This project's own codebase: CLAUDE.md, PROJECT.md, memory/project-algo-bnf-vision.md, memory/risk-gates-tighten-not-stop.md, memory/manual-override-controls.md, memory/referral-discount-verification.md, memory/multi-broker-architecture-direction.md, memory/strategy-analysis-and-simplification-directive.md

### Secondary (MEDIUM confidence)
- AWS Whitepaper — SaaS Tenant Isolation Strategies (silo/pool/bridge models)
- AWS — control-plane/data-plane guidance, NAT Gateway egress, ECS Fargate networking, RLS multi-tenant isolation
- Temporal Docs — multi-tenant application patterns
- WorkOS — SaaS multi-tenant architecture, cryptographic key isolation
- SEBI regulatory summaries — Motilal Oswal, HDFC Sky, Liquide, Algotest, Fyers, Groww, Zerodha, Upstox, Mondaq, QuotaGuard (cross-checked across independent publishers, converge on static-IP/April-2026/RA-registration themes)
- Cognito vs. Auth0 vs. Clerk pricing/DX comparisons — Security Boulevard, guptadeepak.com, zuplo.com
- Streak/Tradetron/Collective2/myfxbook competitor feature comparisons — Algotest, Brokeree

### Tertiary (LOW confidence)
- Public trading-bot SaaS security claims (3Commas, Zignaly, Coinrule) — marketing/support-page claims only, no confirmed worker-isolation blueprint
- Single-source claim that SEBI may require broker-owned infrastructure hosting for algos — needs primary-source or counsel verification before treating as architectural constraint

---
*Research completed: 2026-09-30*
*Ready for roadmap: yes*
