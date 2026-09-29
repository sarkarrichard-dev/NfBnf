# Architecture Research — Multi-Tenant Trading SaaS

**Domain:** Multi-tenant algorithmic trading platform (money-on-the-line, per-tenant brokerage accounts — India NSE/BSE/MCX via Dhan, crypto perps via Delta Exchange India)
**Researched:** 2026-09-30
**Confidence:** MEDIUM overall — the general multi-tenant-SaaS and isolation patterns below are well-documented (AWS's own whitepaper, Temporal's own docs) and cross-checked across independent sources, so those pieces are MEDIUM/verified. No public trading-bot SaaS (3Commas, Zignaly, Coinrule) publishes its internal worker-isolation architecture, so the "how real trading-bot platforms actually do this" pieces are inference from their public security claims (API keys encrypted per-user) plus the general SaaS-isolation literature, not a confirmed blueprint — flagged LOW/inferred where that applies. The SEBI infra-hosting language below is a single-pass web summary of a regulatory circular, not a primary-source read — treat as a **flag to verify with counsel**, not a settled fact.

## Bottom line up front

QuantHawk's own tentative plan — isolated worker container per paying customer, a shared control plane for auth/dashboard/billing, all workers egressing one static Elastic IP — **matches the standard shape for this exact problem.** It's what AWS's own SaaS architecture guidance calls the **"bridge model"**: pool (shared) the low-risk layers, silo (dedicated) the layer that can lose someone's money. Don't second-guess that shape. What's below is (1) confirmation with sources, (2) the parts the plan doesn't yet specify — the interim data layer, the tenant-context boundary, the migration order — and (3) one regulatory item worth checking before building around it.

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    CONTROL PLANE (pooled, shared)                    │
│  ┌───────────┐ ┌────────────┐ ┌──────────┐ ┌───────────────────┐     │
│  │   Auth /   │ │  Billing / │ │ Tenant   │ │  Shared Dashboard  │     │
│  │  Sessions  │ │ Subscription│ │ Registry │ │  (React/Vite, one  │     │
│  │            │ │            │ │ /Config  │ │  build, tenant-    │     │
│  │            │ │            │ │          │ │  scoped by token)  │     │
│  └─────┬──────┘ └─────┬──────┘ └────┬─────┘ └─────────┬──────────┘     │
│        │              │             │                 │               │
│        └──────────────┴─────────────┴─────────────────┘               │
│                        Control-plane Postgres                         │
│              (tenants, subscriptions, users, audit log)               │
└──────────────────────────────┬─────────────────────────────────────────┘
                                │ provisions / reads status from
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   DATA PLANE (siloed, per-tenant)                    │
│  ┌────────────────────┐   ┌────────────────────┐   ┌───────────────┐ │
│  │ Tenant A Worker     │   │ Tenant B Worker     │   │ Tenant C ...  │ │
│  │ container           │   │ container           │   │               │ │
│  │ (today's FastAPI    │   │ (today's FastAPI    │   │               │ │
│  │  app, unmodified    │   │  app, unmodified    │   │               │ │
│  │  core logic)        │   │  core logic)        │   │               │ │
│  │ ┌────────────────┐  │   │ ┌────────────────┐  │   │               │ │
│  │ │ Tenant A's own  │  │   │ │ Tenant B's own  │  │   │               │ │
│  │ │ SQLite journal  │  │   │ │ SQLite journal  │  │   │               │ │
│  │ │ + secrets ref   │  │   │ │ + secrets ref   │  │   │               │ │
│  │ └────────────────┘  │   │ └────────────────┘  │   │               │ │
│  └──────────┬───────────┘   └──────────┬───────────┘   └───────┬───────┘ │
│             │  outbound only            │                      │        │
│             └───────────────┬───────────┴──────────────────────┘        │
│                              ▼                                          │
│                     NAT Gateway — ONE Elastic IP                        │
└──────────────────────────────┬───────────────────────────────────────────┘
                                │ each tenant whitelists this one IP
                                ▼
                    Dhan API / Delta Exchange India API
                    (per-tenant API key, held in Secrets Manager)
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|-------------------------|
| Control plane | Signup, login/session, subscription/billing state, tenant registry (which container, which plan, on/off), admin view, shared dashboard shell | One shared FastAPI (or separate small) service + one shared Postgres. Never holds broker credentials or executes trades itself. |
| Tenant registry | Source of truth for "which container/worker belongs to which paying customer, is it running" | A table in control-plane Postgres, keyed by `tenant_id`; workers poll or receive events from it, never the reverse |
| Per-tenant worker | Runs the actual scan → plan → execute loop against **one** tenant's broker account, one strategy config, one risk budget | A container (ECS task / Fargate task / small EC2, one per tenant) running today's `index_ai` FastAPI app close to as-is, parameterised by tenant config instead of `.env` |
| Per-tenant data store | That tenant's trade journal, ML model state, risk/kill-switch state | SQLite-per-container is fine at low tenant counts (matches today's code almost unchanged); Postgres-per-tenant-schema or RLS-filtered rows once tenant count or dashboard-aggregation needs outgrow per-container SQLite |
| Secrets/credential vault | One broker API key + secret per tenant, never readable across tenants | AWS Secrets Manager, one secret path per tenant (`tenant/{id}/dhan`, `tenant/{id}/delta`), IAM policy scoped so a worker's task role can only read its own tenant's path |
| Shared egress | A single IP address every tenant whitelists once on their own broker account | One NAT Gateway (or equivalent) with one Elastic IP; every tenant worker's subnet routes outbound through it — this is a standard, well-documented AWS pattern, not a novel design |
| Shared dashboard | One UI codebase, tenant-scoped by auth token, admin toggle for the operator's own view | Same React/Vite app already built; reads from the tenant's own worker via an API the control plane proxies/authorizes, not a straight direct connection |

## Recommended Project Structure

Given the codebase already exists as one FastAPI app (`index_ai/`) plus one dashboard (`dashboard/`), the practical structure is **not a rewrite into new repos** — it's splitting what already exists into two roles that get deployed differently:

```
index_ai/                    # UNCHANGED core — becomes the per-tenant worker image
├── scanner.py                # per-tenant: reads that tenant's config, not global .env
├── executor.py, dhan_orders.py, ...
├── config.py                 # becomes tenant-config-aware (env injected per container,
│                              #   not one shared .env file)
└── server.py                 # worker's own small API, called only by the control plane
                               #   (or the dashboard, via the control plane's proxy)

control_plane/                # NEW, small — the one genuinely new service
├── auth.py                   # login/session, replaces single DASHBOARD_PASSWORD gate
├── billing.py                # subscription state (Stripe or similar)
├── tenants.py                # registry: tenant → container/worker mapping, plan, status
├── provisioning.py           # spins up / tears down a tenant's worker container
└── proxy.py                  # routes an authenticated tenant's dashboard calls to
                               #   *their* worker only — the one place tenant_id
                               #   must be checked on every request

dashboard/                    # LARGELY UNCHANGED — same React/Vite app
├── src/...                   # existing admin view stays; add a tenant-scoped view
                               #   that hits control_plane's proxy, not a worker directly
```

### Structure Rationale

- **`index_ai/` stays the worker image, not a rewrite.** The core trading logic (scanner, planner, executor, risk manager, strategy lab) is domain-hard-won and already tested (660 tests). The multi-tenancy problem is almost entirely in *how many copies run and who they talk to*, not in trading logic — so the migration should touch **plumbing** (config loading, credential source, DB path) far more than **strategy code**.
- **`control_plane/` is intentionally small.** Its only jobs are identity, billing, and "which worker does this request belong to." Anything that could arm an order or touch broker credentials does **not** belong here — that's the whole point of the silo.
- **One dashboard build, two audiences.** Don't fork the dashboard into "admin app" and "customer app" as separate codebases — that doubles UI maintenance for no isolation benefit (the dashboard is presentation, not a blast-radius boundary). Scope by auth/role inside the one app, same convention the codebase already half-has (today's admin-only dashboard becomes one role among several).

## Architectural Patterns

### Pattern 1: Silo-per-tenant for the money path, pool for everything else (AWS "Bridge Model")

**What:** Don't pick one isolation model for the whole system. Decide per layer. The layer that can move money or hold credentials (the worker) is siloed — one dedicated container per tenant. The layer that can't (auth, billing, tenant registry, the dashboard's static assets) is pooled — one shared service for everyone.
**When to use:** Any system where a subset of functionality is high-consequence (financial, PII-heavy, safety-critical) and the rest is ordinary CRUD. This is exactly QuantHawk's shape.
**Trade-offs:** Costs more per tenant than a fully pooled system (each tenant is a running container, not a row filtered by `WHERE tenant_id`) — but for money-moving software that cost buys a real thing: one tenant's bug, stuck event loop, or bad strategy state literally cannot touch another tenant's process, memory, or broker session. A fully pooled "shared process + `tenant_id` on every query" model is cheaper but means a single unhandled exception, memory leak, or a forgotten `WHERE tenant_id` clause anywhere in ~14k lines of trading logic can cross tenants. Given this codebase's own history (two real production incidents from blocking I/O in an async handler; a `.env` password-gate bug that sat unenforced for months undetected), the cost of the cheaper model is a genuine risk, not a theoretical one.
**Confidence:** MEDIUM — AWS's own SaaS Tenant Isolation Strategies whitepaper documents this bridge/mixed pattern explicitly as the standard answer when different layers carry different risk.

### Pattern 2: Control plane / data plane split with explicit tenant-context propagation

**What:** The control plane never executes a trade or holds a broker secret. It only tells the data plane (the worker) what it's allowed to do, and tells the dashboard which worker to talk to. Every request that crosses from "shared" to "tenant-specific" carries an explicit tenant identifier that's validated at that boundary — established at login, embedded in the auth token, checked before the request reaches a worker.
**When to use:** Any pooled-plus-siloed hybrid. This is the piece QuantHawk's plan doesn't yet spell out: *where* does tenant identity get checked, and is it checked in exactly one place or scattered through the code? It must be exactly one place (the control plane's proxy layer), not re-implemented per endpoint.
**Trade-offs:** Adds one hop (dashboard → control plane → worker instead of dashboard → worker) but that hop is the entire tenant-isolation guarantee for the dashboard side. Skipping it to save a hop is the most common way this pattern gets silently broken.
**Confidence:** MEDIUM — this is the explicit architectural principle named in AWS's own control-plane/data-plane guidance and echoed in Temporal's multi-tenancy docs (tenant identity established at auth, propagated to the compute layer, enforced at the data layer).

### Pattern 3: Shared egress via one NAT Gateway / Elastic IP

**What:** Every tenant worker container sits in a private subnet with no public IP; all outbound traffic (to Dhan, to Delta) routes through one NAT Gateway, which has one Elastic IP. Each customer whitelists that one IP once, on their own broker account.
**When to use:** Any time many isolated workers all need to appear to an external API as coming from a small, stable set of source IPs — which is forced here by SEBI's static-IP mandate for algo API orders (see Sources) and by brokers' own IP-whitelisting features.
**Trade-offs:** A single NAT Gateway has a connection/bandwidth ceiling shared across every tenant behind it — fine at tens of tenants, worth revisiting (multiple NAT Gateways behind multiple Elastic IPs, tenants sharded across them) well before hundreds. This is a scaling knob to turn later, not a reason to avoid the pattern now.
**Confidence:** MEDIUM — this is standard, widely documented AWS networking (ECS/Fargate tasks in a private subnet routing through one NAT Gateway is a named pattern in AWS's own ECS networking guidance), not something specific to trading.

### Pattern 4: Incremental migration via config/credential extraction first, container-per-tenant last

**What:** The monolith doesn't need to be rewritten to get to container-per-tenant. The actual blocking work, in dependency order, is: (1) make every piece of module-global mutable state (`scanner._state`, `config`'s in-process settings, the `lru_cache` on charge rates) become per-instance instead of per-process — this alone is what lets two copies of the *same unmodified code* run side by side without stepping on each other; (2) move the single `.env` + single SQLite path to per-tenant equivalents (still SQLite-per-container is fine at first, see Pattern 5); (3) only then does "one container per tenant" become a deployment decision rather than a rewrite, because the code no longer assumes it's the only instance on the machine.
**When to use:** Migrating an existing working single-tenant system, where the trading logic itself is already tested and must not be touched more than necessary.
**Trade-offs:** Slower to reach "first paying customer" than a from-scratch multi-tenant rewrite would eventually be, but each step ships independently and is individually testable against the existing 660-test suite, and the trading logic risk surface stays untouched throughout. A rewrite risks silently reintroducing bugs already fixed once (the async-blocking-I/O incidents, the auth-gate-never-enforced incident) in code nobody has re-audited yet.
**Confidence:** MEDIUM — the general shape (strangler-fig-style incremental extraction rather than big-bang rewrite) is well-established migration guidance (Fowler's strangler pattern, AWS/Azure's own versions of it) applied here to the specific blockers this codebase already has documented in its own `CONCERNS.md`.

### Pattern 5: SQLite-per-tenant-container as the honest interim step (not Postgres-first)

**What:** QuantHawk's own phased plan (see PROJECT.md Key Decisions) assumed a Postgres cutover has to happen before containerization. It doesn't, if the isolation model is silo-per-tenant: each tenant's worker container can keep its own SQLite file, exactly like today, because there's no cross-tenant query to serve — nothing ever needs `WHERE tenant_id = ?` inside one container, because the container *is* the tenant boundary. Postgres (with `tenant_id` + RLS, or schema-per-tenant) only becomes necessary when something needs to read **across** tenants in one query — e.g. an operator's aggregate view ("show me every tenant's live P&L in one table"), platform-wide billing metering, or a shared ML/analytics layer.
**When to use:** Re-order the migration: containerize with per-tenant SQLite first (smallest diff from today, no new database technology to learn under pressure), add a Postgres-backed control plane for the genuinely cross-tenant concerns (tenant registry, auth, billing) at the same time, and only move a tenant's *own* trading journal off SQLite if/when per-tenant scale (not tenant *count*) demands it.
**Trade-offs:** Per-tenant aggregate reporting (e.g. "total AUM under algo management across all customers") needs a separate rollup path (each worker reports summary metrics to the control plane) rather than one SQL query — a real limitation, but one that's needed anyway even with row-level-security Postgres, because the whole point of the silo is that a control-plane query should not be able to read a worker's own database directly.
**Confidence:** MEDIUM — inference from the AWS bridge-model guidance (storage isolation choice should match the isolation model chosen for that layer) plus the general RLS/schema-per-tenant literature's own stated boundary condition (shared-schema-with-RLS is for "many small tenants sharing one database," which is the opposite of what silo-per-tenant needs).

## Data Flow

### Provisioning flow (new subscriber)

```
Customer signs up (control plane)
    ↓
Control plane: create tenant record, collect broker API key/secret
    ↓
Control plane → Secrets Manager: store credential at tenant/{id}/{broker}
    ↓
Control plane → Provisioning: launch one worker container for this tenant
    (env: tenant_id, secrets-manager path, plan/risk limits — NOT the raw key)
    ↓
Worker boots, reads its own config, connects to Dhan/Delta using its
    Secrets-Manager-scoped credential, starts its own scan loop
    ↓
Customer whitelists the platform's one Elastic IP on their own Dhan/Delta account
    (a one-time manual step on their side, or guided by the dashboard)
```

### Trading flow (steady state, per tenant — unchanged from today, just containerized)

```
Worker's own scanner._scan_index loop
    ↓
planner.plan_instrument → strategy_router → plan_builder
    ↓
executor.execute_plan → dhan_orders (out through the shared NAT Gateway IP)
    ↓
Worker's own SQLite journal (trade recorded)
    ↓
Dashboard (via control-plane proxy, tenant-scoped auth) reads that tenant's
    worker for live status / journal, never another tenant's
```

### Key Data Flows

1. **Credential flow is one-directional and narrow:** control plane writes a credential once at signup/rotation; a worker only ever reads its own tenant's path; the control plane itself never reads a broker credential back out except to re-provision. No component other than the worker that owns it ever sees a raw broker secret in memory.
2. **Dashboard reads never bypass the control plane's tenant check.** Even though it would be technically simpler to point the dashboard straight at a worker's own small API, doing so means tenant-boundary enforcement has to be correctly re-implemented in every worker instead of once in the proxy. Keep the extra hop.
3. **Aggregate/admin views are a separate, explicit rollup**, not a live cross-tenant query — each worker periodically reports summary numbers (P&L, live/paper state, kill-switch state) to the control plane's own store; the admin dashboard reads that rollup, not the tenants' own databases directly.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 tenant (today) | Current single-process app on one EC2 instance is fine — this is the already-planned "small AWS move," no multi-tenancy needed yet. |
| 2–20 tenants | Container-per-tenant on ECS/Fargate (or plain EC2 + one container each), one shared NAT Gateway/Elastic IP, one small control-plane service + Postgres for tenant/auth/billing. Per-tenant SQLite is fine. This is the regime the existing plan targets and it's the right shape for it. |
| 20–100 tenants | Watch the single NAT Gateway's connection ceiling; consider sharding tenants across a small number of NAT Gateways/Elastic IPs (each customer's own broker whitelist would then need to know which shard's IP applies to them — plan this before it's needed, since it changes the "one IP" customer-facing promise). Consider moving cross-tenant admin/rollup queries to a proper aggregation store if ad hoc per-worker polling gets slow. |
| 100+ tenants | Container orchestration becomes a real operational job (task placement, per-tenant resource limits, autoscaling the fleet) — this is where a full orchestrator (ECS with real scaling policies, or Kubernetes) earns its complexity. Also the point at which the regulatory/SEBI registration questions (this is no longer "one guy's side project," it's a registered intermediary-scale operation) become unavoidable, independent of the tech. |

### Scaling Priorities

1. **First bottleneck: module-global mutable state, not infrastructure.** Before any of the above matters, the actual first blocker is that the current code assumes it's the only copy running on the machine (module-level `_state`, `lru_cache`, one `.env`). That has to be fixed for *even two tenants on two separate containers* to be safe — this is Pattern 4's step 1 and it's needed at tenant #2, not tenant #100.
2. **Second bottleneck: the shared NAT Gateway's throughput**, once tenant count is high enough that many workers polling Dhan/Delta simultaneously saturate one gateway. Not a concern at the tenant counts QuantHawk is planning for next.

## Anti-Patterns

### Anti-Pattern 1: Shared process + `tenant_id` branching for the trading loop itself

**What people do:** Keep one running FastAPI process, add a `tenant_id` column everywhere, and loop over tenants inside the same scanner process to save infrastructure cost.
**Why it's wrong:** Every module-global (`scanner._state`, in-memory kill-switch flags, the charge-rate cache) becomes a cross-tenant hazard the moment two tenants share a process — a stuck loop, an unhandled exception, or a race in one tenant's code path can stall or corrupt another tenant's live trading. For a system that arms real money orders, this is the exact "one tenant's bug touches another tenant's money" failure this research question was asked to prevent.
**Do this instead:** Silo the worker (Pattern 1). Pay the container-per-tenant cost; it's the isolation the money-path requires.

### Anti-Pattern 2: Rewriting the trading engine to "do multi-tenancy properly" in one pass

**What people do:** Treat the multi-tenancy migration as a green-field rewrite opportunity — new framework, new data model, new strategy abstraction, all at once.
**Why it's wrong:** The trading logic is the part of this codebase that's actually been validated against real money and real journal data (the whole point of QuantHawk's "never trust a number that wasn't measured live" discipline). A rewrite re-introduces risk into code that's already earned its correctness the hard way, and re-litigates decisions (2-leg-only spreads, trailing-stop point sizing, per-strategy readiness gates) that took real trading cycles to reach.
**Do this instead:** Pattern 4 — extract config/credentials/state to be per-instance first, keep the trading logic itself byte-for-byte close to what's running today, and let containerization be a deployment change, not a logic change.

### Anti-Pattern 3: One shared broker credential / one shared IP-whitelist relationship for all tenants

**What people do:** For speed, route every tenant's orders through the platform operator's own single Dhan/Delta account (a pooled-broker model) instead of each tenant connecting their own account.
**Why it's wrong:** This is explicitly the model QuantHawk already rejected (see PROJECT.md: "each subscriber connects their own broker account… rather than trading through a pooled account") — and for good reason: a pooled-broker model in India is a much heavier regulatory lift (it starts to look like managing client money, not just providing software), whereas each customer trading through their own broker relationship keeps the platform in the "software provider" posture SEBI's algo-trading framework is written around.
**Do this instead:** Keep per-tenant broker credentials (already decided); the one *shared* thing is the outbound network path (the Elastic IP), not the account.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Dhan (NSE/BSE/MCX) | Per-tenant API key/secret, orders placed from the worker container, outbound through the shared Elastic IP which the tenant whitelists on their own Dhan account | SEBI's 2025/26 algo-trading circular requires a static IP registered per API-trading client and daily re-authentication — both need to be modeled explicitly in the provisioning/worker-boot flow, not left implicit. One web-search pass also surfaced language suggesting algos may need to be hosted on **broker-owned infrastructure**, which — if accurate — would be a material constraint on the "our own AWS worker per tenant" model. This is a single-source, non-primary read; **verify directly against the SEBI circular text or with counsel before this shapes the architecture further**, per the project's own standing note that distribution work needs SEBI counsel. |
| Delta Exchange India | Per-tenant API key/secret, same worker-container pattern | India-registered entity with INR wallet; same static-IP logic likely applies once the platform trades real money for multiple people, even though Delta isn't SEBI's remit directly — confirm Delta's own API/IP-whitelisting requirements don't conflict with a single shared platform IP across many customers' individual Delta accounts. |
| AWS Secrets Manager | One secret path per tenant per broker, IAM-scoped so a worker's task role reads only its own tenant's path | Matches the project's own already-decided choice (see PROJECT.md Key Decisions: "Secrets Manager, not self-hosted Bitwarden") — this research confirms that decision is the standard answer, not just the cheapest one. |
| Billing/subscriptions | Stripe or equivalent, called only from the control plane | Not yet decided in the project's own docs; standard SaaS practice, no trading-specific wrinkle. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Control plane ↔ tenant worker | Provisioning API (control plane → worker's lifecycle) + a narrow status/read API (worker → control plane, summary metrics only) | Never a raw database connection between the two; never the control plane calling into a worker's arming/order endpoints directly except through the same auth path a human would use. |
| Dashboard ↔ control plane ↔ worker | Dashboard always talks to the control plane; control plane proxies to the correct worker after checking the request's tenant matches the authenticated session | This is the one place a bug creates real cross-tenant exposure if skipped — see Pattern 2. |
| Worker ↔ its own SQLite journal | Direct, in-container, unchanged from today | No cross-worker access, by construction (separate containers, separate volumes). |
| Worker ↔ Secrets Manager | IAM task role scoped to exactly that tenant's secret path | The actual enforcement point for "tenant A's container cannot read tenant B's broker key" — a container boundary alone doesn't guarantee this if IAM policy is too broad. |

## Sources

- [AWS Whitepaper — SaaS Tenant Isolation Strategies (silo/pool/bridge models)](https://docs.aws.amazon.com/pdfs/whitepapers/latest/saas-tenant-isolation-strategies/saas-tenant-isolation-strategies.pdf) — MEDIUM confidence, official AWS architecture guidance, directly names the bridge model QuantHawk's plan already resembles.
- [AWS — Manage tenants across multiple SaaS products on a single control plane](https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/manage-tenants-across-multiple-saas-products-on-a-single-control-plane.html) — MEDIUM, control-plane/data-plane separation and tenant-context propagation principle.
- [WorkOS — The developer's guide to SaaS multi-tenant architecture](https://workos.com/blog/developers-guide-saas-multi-tenant-architecture) — MEDIUM, cross-checks the control-plane/data-plane framing.
- [WorkOS — Cryptographic key isolation in multi-tenant SaaS](https://workos.com/blog/cryptographic-key-isolation-multi-tenant-saas) — MEDIUM, per-tenant KEK pattern, confirms Secrets-Manager-per-tenant-path approach.
- [Temporal Docs — Multi-tenant application patterns](https://docs.temporal.io/production-deployment/multi-tenant-patterns) and [Multi-tenancy (Temporal feature)](https://docs.temporal.io/evaluate/development-production-features/multi-tenancy) — MEDIUM, official docs, per-tenant task-queue isolation is directly analogous to per-tenant worker containers.
- [AWS — Using the NAT Gateway for centralized IPv4 egress](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/using-nat-gateway-for-centralized-egress.html) and [AWS ECS Fargate task networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html) — MEDIUM, confirms the single-Elastic-IP-for-many-containers pattern is standard, not novel.
- [AWS — Multi-tenant data isolation with PostgreSQL Row Level Security](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/) — MEDIUM, used to establish the boundary condition (RLS is for many-small-tenants-one-database, i.e. the opposite regime from silo-per-tenant).
- SEBI algo-trading static-IP / broker-infrastructure summary — **LOW, single-pass aggregated web summary, not a primary-source circular read.** Multiple secondary sites (Groww, Fyers, Upstox, QuotaGuard, uTradeAlgos) converge on "static IP must be registered per API client, effective April 2026" but the "broker-owned infrastructure only" claim appeared in only one non-primary source. Flagged for direct verification against the actual SEBI circular (`SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013`) or counsel before it's treated as a hard architectural constraint.
- Public trading-bot SaaS security claims (3Commas, Zignaly, Coinrule) — **LOW, marketing/support-page claims, not verified architecture.** Used only to confirm that "per-user encrypted API key, platform never withdraws funds" is the table-stakes claim in this product category; none publish worker-isolation internals, so no confirmed blueprint exists to copy from this angle — the AWS/Temporal general-SaaS guidance is the stronger source for the actual isolation *mechanism*.

---
*Architecture research for: Multi-tenant algorithmic trading SaaS (QuantHawk / Algo BNF)*
*Researched: 2026-09-30*
