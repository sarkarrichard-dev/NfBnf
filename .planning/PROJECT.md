# QuantHawk (Algo BNF)

## What This Is

QuantHawk is Richard's algorithmic trading platform, currently running single-user
on his own Windows PC. It trades three markets through one FastAPI backend
(`index_ai/`) and one React/Vite dashboard: Indian index options/futures on NSE/BSE
via the Dhan broker (NIFTY, BANKNIFTY, SENSEX — always all three, never one),
crypto perpetuals (and now a first crypto options strategy) on Delta Exchange
India, and MCX commodity futures also via Dhan. It is headed toward subscription
distribution to other traders, each connecting their own broker account.

## Core Value

Never presents a strategy as ready for real money until it has been measured —
against real broker charges, on the real live journal, not a backtest — to
actually make money. The project has been burned before by numbers that looked
good and weren't; the discipline of insisting on real, measured evidence before
trusting a result is the thing that must not slip.

## Business Context

- **Customer**: retail Indian traders (and, on the crypto side, Delta Exchange
  India users) who want to run proven algo strategies without building them
  themselves.
- **Revenue model**: subscription SaaS. Each subscriber connects their own
  broker account (Dhan for India, Delta for crypto today; more brokers planned —
  see Key Decisions) rather than trading through a pooled account.
- **Success metric**: not yet pinned down precisely. The nearest working proxy
  today is the per-(strategy, instrument) live scorecard clearing its own
  readiness bar (30+ trades, 14+ trading days, net-positive) before anything is
  proposed for real money.
- **Strategy notes**: `memory/project-algo-bnf-vision.md`,
  `memory/multi-broker-architecture-direction.md` (this project's own persistent
  memory, carried session to session).

## Requirements

### Validated

<!-- Built, working, and relied on today — inferred from the codebase map plus
     confirmed live usage, not aspirational. -->

- ✓ Indian index options/futures scanning, paper and live-gated trading on
  NIFTY/BANKNIFTY/SENSEX via the legacy `scanner → planner → strategy_router →
  executor → dhan_orders` engine — the one canonical India engine;
  `options_cpr/` is backtest-only tooling, not a live path.
- ✓ Crypto paper trading on Delta perps across five strategies (`ny_n_break`,
  `ichimoku`, `ak_roxx_pro`, `cpr_trend`, `rsi_adx_trend`), with live-arming
  gated per (strategy, coin) pair against a measured readiness bar — arming
  crypto does not send every strategy/coin live at once.
- ✓ MCX commodity futures paper trading (crude/gas/gold/silver), reusing the
  index directional signal rather than the crypto strategies, evening session
  while the equity scanner is shut.
- ✓ A real trailing-stop-loss *and* a separate trailing-profit mechanism on
  every segment — a standing, non-negotiable requirement, not optional per
  strategy.
- ✓ Per-(strategy, instrument) Strategy P&L scorecard, built from the live
  journals (not backtests) so the same logic's performance can be judged
  separately on each instrument.
- ✓ Real broker-cost modelling applied to every recorded trade — Dhan's
  ₹20/order + STT + exchange txn + SEBI + GST + stamp schedule for India,
  Delta's real taker/maker fee + GST for crypto, MCX's schedule for
  commodities — never an abstract "friction" number.
- ✓ Telegram notifications on trade open/close across every segment.
- ✓ A defined dashboard design system under the QuantHawk brand (hawk-gold /
  silver / candlestick-green on near-black, tokenised in
  `dashboard/src/index.css`), held to a high UX bar.
- ✓ Risk gates that tighten exposure under stress rather than fully halting a
  lane, plus kill-switches on consecutive losses / daily loss caps for live
  trading.
- ✓ A first, partial API auth stopgap (`ADMIN_API_SECRET`) in front of the
  endpoints that can arm live orders or write broker credentials — off by
  default, not yet the full per-user auth the multi-tenant destination needs.
- ✓ Three separate ML layers (India live-order gate, crypto model, paper-futures
  "brain" model), each trained only on the real live journal — crypto's
  historical-backtest tuner is explicitly not trusted as ground truth per
  Richard's standing instruction.

### Active

<!-- Being built or decided right now. -->

- [ ] A BTC daily options straddle paper lane (`crypto/btc_straddle.py`,
  added 2026-09-29/30) — sell the ATM call+put on Delta each evening, hold to
  next-day expiry. Deliberately naked/unhedged, the opposite of the project's
  usual 2-leg-hedged-only rule, tested anyway on Richard's explicit
  instruction. Paper only; there is no live path for it yet.
- [ ] Adopting this GSD phase-based planning process itself, replacing pure
  turn-by-turn requests for future work (this document is the first artifact
  of that adoption).
- [ ] Continued strategy discovery and measurement per (strategy, instrument) —
  the option **buy** lane specifically is the acknowledged weak point (needs
  real work to reach Richard's ≥65% precision bar; selling is where his edge
  and comfort are).
- [ ] Moving the app off Richard's own PC onto a personal-use AWS EC2 instance
  with a static Elastic IP — explicitly the *small* version of this move (just
  relocating the existing single-tenant app), separate from and not blocked on
  the larger multi-tenant SaaS migration below.

### Out of Scope

<!-- Explicit boundaries, with reasoning, so they don't get silently re-proposed. -->

- Sideways / range structures (iron condor, naked 4-leg) on the India index
  sell lane — killed by Richard's direction; only 2-leg hedged directional
  credit spreads (`SELL_BULL_PUT_SPREAD` / `SELL_BEAR_CALL_SPREAD`) ship there.
  Fewer legs, lower cost, and it matches how Richard actually trades.
- Trusting a historical backtest as ground truth for crypto strategies —
  Richard rejected this explicitly ("i do not trust past data and testing...
  i want all testing on live data from the market"); the nightly
  historical-backtest auto-tuner stays off by default, diagnostic only.
- A long list of specific strategies already tested and measured
  net-negative — full list and numbers in `memory/strategy-findings.md`.
  Do not re-propose or rebuild without new information; the standing prior
  on any new social-media/indicator strategy is "probably no edge" until
  proven otherwise with real costs.
- Full multi-tenancy (per-customer isolation, per-user credential vault,
  Postgres, containerised workers) — not started. This is the acknowledged
  blocker standing between the current single-`.env`/single-SQLite app and
  the subscription destination; see Key Decisions for the agreed shape.
- Deep, general-purpose stock investing (the separately-scoped "future
  investing section" idea) — parked, not part of the current build; see
  `memory/future-investing-section.md` if/when it's picked back up.

## Context

**Technical environment.** FastAPI backend in `index_ai/` (~14k LOC), React +
Vite + TypeScript dashboard in `dashboard/`, both served from one process at
`http://127.0.0.1:8000`. SQLite (WAL mode) for the trade journal, no external
database or cache yet. Local gitignored `memory/` holds the real journals, ML
models, and market-context caches — separate from `.planning/`'s own tracked
planning docs. Windows/PowerShell is the actual dev environment; `.env` is
permission-blocked in Claude sessions, so settings changes go through the
running app's `/api/settings/features` path or are handed to Richard as an
exact line to paste in himself.

**Brokers.** Dhan for India (NSE/BSE F&O and MCX), Delta Exchange India for
crypto. `reference/openalgo/` is a vendored, read-only submodule from the same
author as `openbull` (noted as a future reference for the multi-broker
plumbing phase) — check it before implementing any Dhan protocol detail; it
has already caught one genuinely counterintuitive field-ordering bug in
Delta's live websocket feed.

**Known technical debt** (full detail in `.planning/codebase/CONCERNS.md`,
mapped 2026-09-30): the multi-tenancy blocker itself, a hardcoded NSE/MCX
holiday calendar needing a yearly refresh, the India option backtest's
Black-Scholes-proxy limitation (no real historical option chain), a
single-instance/SQLite scaling ceiling, no per-strategy kill switch yet, and
some remaining security debt (the admin-secret auth gate is optional/off by
default, `.env` secrets are plaintext on disk).

**Regulatory.** Distributing algo-trading software to other people in India is
SEBI-regulated territory (research-analyst / investment-adviser registration,
2025 SEBI rules on retail algo trading requiring broker approval and
per-strategy identifiers, a static-IP mandate). This is a real constraint on
the business model, to be raised whenever distribution work is planned, not
an afterthought.

**Codebase intelligence.** A `graphify` knowledge graph of the codebase already
exists — query it for architecture/relationship questions rather than
re-deriving them from scratch.

## Constraints

- **Tech stack**: Python 3.11+/FastAPI/SQLite backend, React/Vite/TypeScript
  dashboard — already in place, not up for renegotiation without a strong
  reason.
- **Regulatory**: SEBI oversight on algo-trading distribution in India —
  affects the subscription launch timeline and structure, not just a detail.
- **Money-path safety**: real orders require two independent locks
  (`TRADING_MODE=LIVE` and `ALLOW_LIVE_TRADING=true`), the exact arming phrase
  "ARM LIVE ORDERS", and switching to Paper always disarms. Never put blocking
  I/O in an `async def` handler — this has caused real incidents twice already.
- **Budget**: infrastructure choices are budget-conscious by default, though
  Richard has explicitly overridden the cheaper option before when he judged
  it worth it (see Key Decisions — AWS over Hetzner).
- **Windows dev environment**: PowerShell syntax only when handing Richard a
  command; `.env` cannot be read or written directly in these sessions.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| AWS EC2 + S3 for hosting, not Hetzner | Richard explicitly chose AWS after being shown the cost gap (~$11/mo Hetzner vs $150+/mo naive AWS) — a deliberate override of the budget-first recommendation | — Pending (personal EC2 move not yet done) |
| Multi-tenancy shape: isolated worker container per paying customer + shared control plane, all workers egressing one Elastic IP | Isolation is a safety property for money-path software, not just an engineering nicety; one shared IP also satisfies the SEBI static-IP mandate for every customer at once | — Pending (not started) |
| Credential vault: AWS Secrets Manager, not self-hosted Bitwarden | A full password-manager product is the wrong-sized tool for "one encrypted broker API key per customer"; Secrets Manager matches the AWS-hosting decision already made | — Pending (not built) |
| Crypto's ML/strategy stack kept fully separate from India's (no shared model code, only shared UI layout / notify / config / market_clock / Ichimoku indicator math) | Different broker, different instruments, different risk shape — sharing would couple two things that should fail independently | ✓ Good — implemented as intended |
| Crypto sized by universal `$ margin per position`, India by lots | A NIFTY lot has cultural meaning; a crypto "lot" doesn't, and contract values differ wildly per coin, so a dollar budget is the only sizing that's comparable across coins | ✓ Good — implemented |
| Live-only testing for crypto — no historical backtest trusted as ground truth | Richard explicitly rejected trusting downloaded historical candles for strategy tuning; only the real live journal counts | ✓ Good — standing rule, enforced (nightly auto-tuner off by default) |
| India index sell lane is 2-leg hedged credit spreads only, no naked, no sideways/iron-condor | Fewer legs means lower real brokerage/STT/slippage, and it matches how Richard actually trades directionally | ✓ Good — standing rule (one deliberate crypto-side exception: the new BTC straddle test) |

---
*Last updated: 2026-09-30 after initialization — Richard has not yet reviewed this draft; corrections expected.*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state
