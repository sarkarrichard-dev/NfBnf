# Stack Research

**Domain:** Multi-tenant fintech SaaS — subscriber-facing algo-trading platform (each subscriber connects their own broker account)
**Researched:** 2026-09-30
**Confidence:** MEDIUM (web-search cross-checked across multiple independent sources; no official vendor docs pulled via Context7 in this pass — treat version numbers as current-as-of-search-date, re-verify against official docs before pinning in code)

## Scope note

This is a **subsequent milestone** on an existing single-tenant system (FastAPI/SQLite, `index_ai/` + `dashboard/`). Nothing here proposes replacing the working trading logic, the dashboard framework, or the brokers. It only covers the four things the milestone question asked about: tenant data isolation, the credential vault, subscriber/admin auth, and per-tenant worker isolation — the four blockers named in `PROJECT.md`'s Key Decisions and Out of Scope sections.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| PostgreSQL | 16.x or 17.x (RDS/Aurora) | Replaces SQLite as the tenant-aware trade journal | SQLite has no concept of concurrent multi-writer tenants or row-level access control. Postgres has native Row-Level Security (RLS) that enforces tenant boundaries *inside the database kernel* — even a buggy query or a forgotten `WHERE tenant_id = ...` can't leak another subscriber's trades. This is the single highest-leverage change for the multi-tenancy blocker. |
| SQLAlchemy | 2.0.44+ (2.0 async style) | ORM / query layer, `asyncpg` driver | Already Python/FastAPI; 2.0's async engine pairs with FastAPI's async handlers (the project's own CLAUDE.md flags blocking I/O in `async def` as a recurring real incident — async SQLAlchemy + asyncpg avoids reintroducing that class of bug when SQLite's sync writes are replaced). |
| asyncpg | 0.31.0+ | Async Postgres driver under SQLAlchemy | Fastest Postgres driver for Python async; standard pairing with SQLAlchemy 2.0 async. |
| Alembic | 1.17.1+ | Schema migrations | Already the de facto standard for SQLAlchemy projects; needed the moment there's a real schema instead of ad-hoc SQLite tables. |
| AWS Secrets Manager | current API (boto3 `secretsmanager` client) | Per-subscriber broker credential vault | Already decided in `PROJECT.md` (Key Decisions) — this research **confirms it**, see Sanity Check below. |
| Amazon ECS on AWS Fargate | current | One isolated worker container per paying subscriber | Already decided in `PROJECT.md` — confirmed as the standard 2025/2026 pattern, see below. Each Fargate task is its own Firecracker micro-VM: separate network namespace, no lateral movement between subscribers' containers, matches the "isolation is a safety property for money-path software" rationale already recorded. |
| AWS Step Functions | current (Standard workflows) | Multi-step tenant lifecycle workflows (onboard subscriber → validate broker keys → provision worker → arm) | Not yet decided in PROJECT.md — new recommendation, see Background-Job Patterns below. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `aws-secretsmanager-caching` | 1.1.3 | Client-side caching wrapper around Secrets Manager reads | Every worker container fetches its subscriber's broker keys on startup; this avoids a Secrets Manager API call (and its cost + latency) on every restart/scan cycle. Refreshes on a TTL, not never — don't hand-roll a cache that never expires a rotated secret. |
| `boto3` / `botocore` | latest | AWS SDK — Secrets Manager, ECS/Fargate task control, Step Functions, KMS | Already implied by AWS EC2/S3 decision; this extends it to Secrets Manager, ECS task provisioning, and KMS. |
| `python-jose[cryptography]` or `PyJWT` | latest | Verify JWTs issued by the auth provider inside FastAPI middleware | Whichever auth provider is chosen (Cognito or Clerk, see below) issues JWTs; FastAPI needs to verify the signature and pull `tenant_id`/`sub` claims without a DB round-trip per request. |
| Celery + Redis (already-adjacent pattern, not yet in this repo) | Celery 5.x | Single-step background tasks (send Telegram alert, refresh a cache, poll a broker status) | Keep for simple, stateless, idempotent jobs. Do **not** reach for this for the multi-step tenant-onboarding workflow — that's a Step Functions job, not a Celery job (see rationale below). |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `pg_dump` / RDS automated snapshots | Backup for the new Postgres journal | SQLite's file-copy backup story goes away; RDS gives point-in-time recovery for free, which a money-path journal needs anyway. |
| Terraform or AWS CDK | Provisioning ECS task definitions, Secrets Manager entries, Step Functions state machines per subscriber | Manually clicking through the AWS console to provision each new subscriber does not scale past a handful of customers; this needs to be code from day one of the multi-tenant build, not retrofitted later. |

## Installation

```bash
# Core (add to requirements.txt / pyproject.toml)
pip install "sqlalchemy[asyncio]==2.0.44" asyncpg==0.31.0 alembic==1.17.1
pip install boto3 aws-secretsmanager-caching==1.1.3
pip install "python-jose[cryptography]"  # or: pip install PyJWT

# Dev dependencies
pip install pytest-postgresql  # local Postgres fixture for tests, replaces the current SQLite-in-tests pattern
```

## Multi-Tenant Data Isolation: the decision

**Recommendation: shared Postgres database, shared schema, Row-Level Security (RLS) as the enforcement layer — not per-tenant schema, not per-tenant database.**

Why, specifically for this project:

- The project's own subscriber base is retail traders paying a subscription — this is the "many small tenants" shape (hundreds to low thousands), not "a handful of enterprise accounts each needing a dedicated compliance boundary." RLS is the documented default for that shape; schema-per-tenant becomes an operational migration nightmare (every schema change has to run N times) well before subscriber count gets interesting.
- **Defense in depth, not either/or**: scope every query at the application layer (a FastAPI dependency that injects a tenant-scoped session, reading `tenant_id` out of the verified JWT) *and* enforce it again with a Postgres RLS policy on every table. If the app-layer filter is ever forgotten in one new endpoint, RLS is the backstop that prevents an actual cross-tenant leak rather than just a bug report.
- This does **not** conflict with the already-decided "isolated worker container per subscriber" — those are two different isolation layers solving two different problems. The Fargate container isolates *compute/execution* (one subscriber's trading bot process can't touch another's memory or network). RLS isolates *data* (the shared journal database can't leak rows across tenants even though it's one database serving every worker). Both are needed; neither substitutes for the other.
- Database-per-tenant would technically also work and is the "if in doubt, safest" answer for a money-path system, but it multiplies operational cost (N RDS instances or N databases to patch, back up, monitor) for a subscription price point that's presumably not enterprise-tier. Revisit database-per-tenant only if a specific large customer contractually demands physically separate storage — don't build it as the default.

## Credential Vault: sanity-checking the AWS Secrets Manager decision

**The already-recorded decision holds up.** AWS Secrets Manager is the standard, correctly-sized tool for "one encrypted broker API key/secret per subscriber," confirmed against current fintech multi-tenant guidance. Three refinements worth adding to the existing decision, none of which reverse it:

1. **Use customer-managed KMS keys (CMKs), one per tenant or per tenant-tier, not the default AWS-managed key.** A per-tenant CMK means a compromised or offboarded subscriber's key material can be disabled/rotated independently, and it gives a real audit trail per tenant for the eventual SEBI conversation. Default AWS-managed keys don't offer that granularity.
2. **Naming convention matters at scale**: `/prod/<service>/<tenant_id>/dhan-credentials` (or `/delta-credentials`) rather than one flat namespace — this is what makes IAM policies per-worker-container scopable (a subscriber's Fargate task should only have `secretsmanager:GetSecretValue` on *its own* secret ARN, not a wildcard over every subscriber's secret — this is the IAM-policy equivalent of the RLS row-boundary above, applied to the vault).
3. **Cache, don't poll.** Use `aws-secretsmanager-caching` in each worker container (fetch on task start, refresh on a TTL — e.g. hourly) rather than calling Secrets Manager on every scan cycle; this matters both for AWS cost (Secrets Manager charges per API call) and for the existing "no blocking I/O in an async handler" lesson — a secrets fetch is exactly the kind of call that must not block the scanner loop.

## Subscriber Dashboard vs Admin Dashboard: Auth Pattern

**Recommendation: Amazon Cognito, not Clerk, not Auth0 — specifically because this project is already committed to AWS and is budget-conscious.**

| Provider | Verdict for this project |
|----------|---------------------------|
| **AWS Cognito** | **Recommended.** Cheapest at any real scale (~$495/mo at 100k MAU vs. Auth0's negotiated-enterprise pricing at that tier — and this project will be nowhere near 100k MAU for a long time, so early cost is near-zero), and it integrates natively with IAM, which is exactly what's needed to scope a subscriber's Cognito identity to *only* their own Fargate task / Secrets Manager entry via IAM policy conditions. Rougher developer experience than Clerk is the real tradeoff — budget for more integration time. |
| Clerk | Viable alternative if dashboard build speed matters more than infra uniformity — best-in-class React/Next.js DX, built-in "Organizations" primitive that maps naturally onto "subscriber vs. admin" role separation, free tier covers early subscriber counts. Reconsider this if the Cognito integration proves to be a real drag on shipping the subscriber dashboard split. |
| Auth0 | **Not recommended for this project.** Its strengths (deep enterprise federation, compliance breadth, fine-grained authorization add-ons) solve problems this project doesn't have yet, at a price point the "budget-conscious by default" constraint doesn't justify. |

**Pattern regardless of provider chosen:** two separate app surfaces, not one dashboard with a hidden admin toggle — a subscriber-facing app and today's existing (admin-only) dashboard, each gated by role claims in the verified JWT. FastAPI reads the role claim in the same middleware that reads `tenant_id`, and routes/endpoints check role, not just presence of a token. This is a new access-control layer on top of the existing `ADMIN_API_SECRET` stopgap named in PROJECT.md — the shared-secret gate was always described there as a stopgap, not the final per-user auth; Cognito/Clerk is that final piece.

## Background-Job / Worker Isolation Pattern

**Recommendation: keep the already-decided "one Fargate container per paying subscriber" for the always-on trading bot process. Add AWS Step Functions specifically for the multi-step *tenant lifecycle* workflows (new decision, not previously recorded in PROJECT.md) — do not reach for Celery or Temporal for that lifecycle work.**

Reasoning:

- **The always-on trading loop itself is not a "background job" in the queue sense** — it's a long-running process (the existing scanner loop, per subscriber). That's what the Fargate-task-per-subscriber decision already covers correctly: each subscriber's container runs its own scanner/planner/executor loop against its own broker credentials, isolated by Fargate's own virtualization boundary (separate network namespace, no cross-container reachability except through an authenticated load balancer). This research confirms that's the standard 2025/2026 shape for this exact case (per-tenant isolated compute for a stateful bot), not something to reconsider.
- **What's missing today, and needs a workflow engine, is the *onboarding/lifecycle* sequence**: a new subscriber signs up → their broker API keys are validated → a Secrets Manager entry is created → a Fargate task definition is provisioned and started → a welcome notification fires → (later) an offboarding sequence tears the above down cleanly. That's a multi-step process with side effects at each step, needing to be resumable/auditable if step 3 fails after steps 1–2 succeeded — exactly the shape Step Functions and Temporal both target, and exactly the shape plain Celery does not (Celery has no durable cross-step state by default).
- **Step Functions over Temporal for this specific project**: the project has already committed to AWS (EC2 + S3, decided explicitly over Hetzner despite cost) and is budget-conscious. Step Functions is a managed, pay-per-transition service with zero extra infrastructure to run; Temporal needs its own server cluster (or Temporal Cloud, a new recurring cost) and has a 2–4 week team ramp-up. Given the AWS-native decision already made and the budget constraint already stated, Step Functions is the lower-total-cost, lower-operational-burden choice for this one workflow. Reconsider Temporal only if the team later needs portable, self-hosted workflow logic outside AWS, or workflows grow far more complex than tenant lifecycle management.
- **Existing simple background tasks stay as they are (or move to Celery/RQ if they need offloading from the request thread) — this is not a call to introduce a workflow engine everywhere.** Telegram notification sends, cache refreshes, and other single-step idempotent jobs do not need Step Functions; using it there would be over-engineering the simple 90% of jobs to solve the complex 10% (tenant onboarding).

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| Postgres shared-schema + RLS | Schema-per-tenant | A specific enterprise/regulated customer contractually requires a physically separable schema; not the default for retail subscription tenants |
| Postgres shared-schema + RLS | Database-per-tenant | SEBI or a future audit explicitly requires physical data separation per customer — revisit if/when SEBI counsel (already flagged as needed in PROJECT.md) raises this |
| AWS Cognito | Clerk | Dashboard build velocity becomes the binding constraint and the team is willing to accept a second vendor outside the AWS bill |
| AWS Step Functions | Temporal | Workflow complexity grows well beyond tenant onboarding/offboarding, or the team wants workflow logic portable off AWS |
| ECS Fargate task per subscriber | Amazon EKS with per-tenant namespaces | Subscriber count grows into the hundreds+ and Kubernetes-level scheduling/bin-packing efficiency starts to matter more than Fargate's simplicity; premature at current or near-term scale |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| SQLite for the multi-tenant journal | No native row-level access control, single-writer lock contention gets worse with concurrent subscriber workers, no managed backup/PITR story | Postgres (RDS/Aurora) with RLS |
| Self-hosted credential vault (Vault, Bitwarden Server, etc.) | Already correctly rejected in PROJECT.md's Key Decisions for being the wrong-sized tool for "one broker key per subscriber" — this research found nothing to overturn that | AWS Secrets Manager (already decided, confirmed above) |
| One shared Fargate/EC2 process running every subscriber's trading loop in-process (thread-per-tenant) | No real isolation boundary — one subscriber's bug or runaway strategy can starve or crash every other subscriber's live-money process; also a direct violation of the "isolation is a safety property for money-path software" rationale already recorded in PROJECT.md | One Fargate task per subscriber (already decided) |
| Celery/Temporal for the always-on per-subscriber trading loop itself | The trading loop is a long-running stateful process, not a discrete job to queue and retry | Fargate task per subscriber (long-running container), reserve Celery/Step Functions for discrete jobs and multi-step workflows respectively |
| Auth0 at this project's current size | Enterprise-tier pricing and feature depth (SSO federation breadth, FGA) the project doesn't need yet, conflicts with the stated budget-conscious constraint | Cognito (cheapest, AWS-native) or Clerk (fastest DX) |

## Stack Patterns by Variant

**If the subscriber count stays under ~50–100 for the first 6–12 months (realistic near-term case):**
- RLS is still correct (no reason to delay it — retrofitting RLS onto an already-live multi-tenant table is much more painful than building it in from the start), but Fargate-per-subscriber cost should be watched closely; consider a scale-to-zero pattern (stop the container outside market hours, since India options trading is session-bound 9:15–15:30 IST anyway) to keep AWS spend proportional to the "budget-conscious" constraint.

**If/when a subscriber wants a broker this project doesn't yet support (the already-flagged multi-broker direction in memory):**
- The Secrets Manager naming convention and per-tenant CMK pattern above extends cleanly (`/prod/<service>/<tenant_id>/<broker_name>-credentials`) — no architecture change needed, just an additional secret per subscriber per broker they connect.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| SQLAlchemy 2.0.44 (async) | asyncpg 0.31.0, Python 3.11+ | Matches the project's already-stated Python 3.11+ constraint from PROJECT.md — no Python version bump needed |
| Alembic 1.17.1 | SQLAlchemy 2.0.x | Standard pairing, no known incompatibilities |
| `aws-secretsmanager-caching` 1.1.3 | boto3 (any recent) | Requires Python 3.7+, well under this project's 3.11+ floor |

## Sources

- Multiple independent 2025/2026 dev.to / Medium articles on Postgres RLS vs. schema-per-tenant vs. database-per-tenant (cross-checked, MEDIUM confidence — no single canonical vendor doc, but strong agreement across sources) — web search, 2026-09-30
- AWS official Secrets Manager best-practices documentation (`docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html`) plus fintech-specific secondary sources — web search, 2026-09-30
- FastAPI multi-tenancy pattern articles (JWT-claim tenant extraction, RLS-backed session, repository-pattern query scoping) — web search, 2026-09-30, MEDIUM confidence
- Cognito vs. Auth0 vs. Clerk 2025/2026 pricing and DX comparisons (Security Boulevard, guptadeepak.com CIAM Compass, zuplo.com pricing breakdown) — web search, 2026-09-30, MEDIUM confidence
- AWS re:Invent whitepapers and blog posts on per-tenant ECS Fargate isolation patterns (`aws.amazon.com/blogs/architecture`, `aws.amazon.com/blogs/containers`) — web search, 2026-09-30, MEDIUM confidence
- Celery vs. Temporal vs. AWS Step Functions comparison articles (readysetcloud.io, hackernoon.com) — web search, 2026-09-30, MEDIUM confidence
- PyPI package pages for exact current versions (FastAPI, SQLAlchemy, asyncpg, Alembic, `aws-secretsmanager-caching`) — web search, 2026-09-30; **re-verify exact pins against pypi.org directly before adding to `requirements.txt`**, since these were read via search snippets, not the PyPI page itself
- `C:/Richard Docx/personal/Algo BNF/.planning/PROJECT.md` — existing project decisions this research sanity-checks against

---
*Stack research for: multi-tenant fintech SaaS (trading platform) — data isolation, credential vault, auth split, worker isolation*
*Researched: 2026-09-30*
