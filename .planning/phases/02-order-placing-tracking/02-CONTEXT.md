# Phase 2: Order Placing & Tracking - Context

**Gathered:** 2026-09-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Harden order placement and position tracking so the code survives the specific
failure modes already flagged as untested in `.planning/codebase/CONCERNS.md`:
a cancel racing a placement, a broker disconnect mid-position, a websocket
drop under load, and an undocumented tick-feed fallback. This phase proves
these failure modes are handled correctly — it does not change trading
strategy logic, does not add new order types, and does not touch anything
under `index_ai/brain/` or `index_ai/strategies/`.

</domain>

<decisions>
## Implementation Decisions

### Broker scope and priority
- **D-01:** Both broker connections are in scope this phase — the shared Dhan
  connection (India options/futures + MCX commodities: `executor.py`,
  `dhan_orders.py`, `dhan.py`) AND the separate Delta Exchange connection
  (crypto). Originally the roadmap's own file references (CONCERNS.md) implied
  Dhan-only; Richard explicitly widened this to include crypto.
- **D-02:** Crypto/Delta is fixed **first**, ahead of Dhan — Richard's stated
  reason: crypto is the one aiming to arm real money soonest (targeting
  ~November 2026 per `memory/project-algo-bnf-vision.md`), so its
  order-placement path should be hardened before it carries real risk, even
  though Dhan has been live longer.

### How fixes are proven
- **D-03:** Tests replay **real recorded broker traffic** (Dhan and/or Delta
  order responses, websocket messages) with deliberately injected faults
  (dropped connection, delayed response, out-of-order delivery) — not a
  from-scratch fake/mock client. **Research must check whether such raw
  traffic recording already exists for either broker** (as opposed to the
  higher-level `market_log.chain` option-chain snapshots, which are a
  different thing) before the planner sizes this; if it doesn't exist yet,
  building the capture is part of this phase's own work, not a prerequisite
  someone else has to do first.
- **D-04:** The proof bar for "fixed" is a **test that simulates the exact
  failure and passes** — no live paper-trading soak period required on top of
  that. This differs from how a trading-strategy change gets proven (the
  40-trade confidence ladder from Phase 1) because this is plumbing
  correctness, not a strategy whose edge needs measuring.

### Sequencing
- **D-05:** Within each broker, the two higher-risk fixes — order-cancel
  races (ORD-01) and position reconciliation after a disconnect (ORD-02) —
  are fixed before the two lower-risk ones — websocket reconnection under
  load (ORD-03) and the tick-feed fallback (ORD-04). Matches
  CONCERNS.md's own priority split (High vs. Medium risk).

### Unclear-state handling (position reconciliation)
- **D-06:** When the code genuinely cannot tell whether an order went through
  after a disconnect, it queries the broker's own order-status API to break
  the tie (Dhan and Delta both expose one), rather than silently assuming
  "not filled." — **Reversibility:** reversible (a runtime decision, not a
  structural commitment).
- **D-07 (Claude's discretion, recorded so it isn't silently invented later):**
  If the order-status tie-breaker API is *also* unreachable during the same
  disconnect, fall back to the safe default Richard didn't need to pick
  explicitly — treat the order as not filled rather than risk a duplicate.
  This is the natural fallback-of-the-fallback; flag it explicitly in the
  plan rather than leaving it implicit.

### Crypto's 24/7 case
- **D-08:** Crypto gets an extra safeguard India/commodities don't need: a
  maximum time an unclear/stuck position can sit before the system forces a
  fresh broker-status check or surfaces it — because crypto has no session
  close, an unclear position could otherwise sit silently for hours. Exact
  timeout value left to Claude's discretion during planning (research/plan
  should pick something short relative to crypto's own scan cadence, not an
  arbitrary round number).

### Notifications
- **D-09:** A detected reconciliation problem (couldn't confirm a position's
  true state, caught a near-duplicate order) sends a Telegram message, the
  same way trade open/close already does — Richard wants to know in real
  time when the money path had to self-correct, not just find it in a log
  later.

### Tick-feed fallback documentation (ORD-04)
- **D-10:** Written up in the existing docs (Strategy-Guide-style) **and** a
  simple dashboard indicator this phase (e.g. "ticks: live" vs. "ticks: candle
  fallback") — not just the guide alone. The fuller "Data Health" dashboard
  view (tick age, chain snapshot age, spread age, full websocket status) is
  Phase 4's job, not this phase's — this phase's indicator should be small and
  not pre-build Phase 4's work.

### Claude's Discretion
- Exact mechanism for capturing/replaying real broker traffic (D-03) — left
  to research to determine what, if anything, already exists.
- Exact crypto stuck-position timeout value (D-08).
- Exact wording/placement of the tick-status dashboard indicator (D-10) —
  should reuse existing dashboard patterns, not invent a new panel type.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The untested edge cases this phase addresses
- `.planning/codebase/CONCERNS.md` — "Money-path edge cases under-tested"
  and "Dhan websocket reconnection under load" sections (the source of
  ORD-01 through ORD-04's scope and priority)
- `.planning/ROADMAP.md` — Phase 2 goal and success criteria (ORD-01..04)
- `.planning/REQUIREMENTS.md` — ORD-01, ORD-02, ORD-03, ORD-04 full text

### Standing project rules this phase must respect
- `CLAUDE.md` — "Money path — extra care" section (this phase touches
  `executor.py`, `dhan_orders.py`, `exit.py`, `scanner.py`'s tick-driven stop
  triggering — all on the caution list); the two-lock live-arming rule must
  stay untouched; never put blocking I/O in an `async def` handler
- `memory/project-algo-bnf-vision.md` — the crypto live-money ~November 2026
  timeline that drove the D-02 priority decision; the crypto lane's own
  architecture (Delta Exchange India, separate from India's Dhan connection,
  no shared ML/strategy code)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `index_ai/executor.py`, `index_ai/dhan_orders.py` — India/commodities order
  placement (shared Dhan connection)
- `index_ai/dhan.py` — Dhan websocket client
- `index_ai/learning.py` — existing reconcile functions to harden, not replace
- `index_ai/scanner.py` — `on_index_tick` handler, poll loop, tick-driven stop
  triggering (`ENABLE_TICK_FEED`)
- `crypto/executor.py`, `crypto/lanes.py` — Delta's order-placement path
  (not yet examined this session — research should trace its exact
  cancel/reconcile/websocket shape before planning, since it may not mirror
  India's structure 1:1)
- `guides/Strategy Guide.md` — existing docs pattern to extend for D-10
- Existing Telegram notification functions in `index_ai/notify.py` (already
  used for trade open/close) — extend the same pattern for D-09, don't build
  a new notification path

### Established Patterns
- Every other risk-related change in this project ships paper-safe by
  default and never bypasses the two-lock live-arming rule — this phase's
  fixes are plumbing correctness, not new trading behavior, so they carry no
  arming risk by nature, but any new code path must still be checked against
  this rule during review.

### Integration Points
- Dhan and Delta each expose their own order-status query endpoint — the
  D-06 tie-breaker call target. Research should confirm the exact endpoint
  name/shape for each broker.

</code_context>

<specifics>
## Specific Ideas

No specific implementation given beyond the decisions above — Richard's input
was on scope, priority, proof bar, and behavior under failure, not on how the
code should be structured.

</specifics>

<deferred>
## Deferred Ideas

- The fuller "Data Health" dashboard view (tick age, option-chain snapshot
  age, measured spread age, full Dhan websocket status all in one place) —
  explicitly Phase 4's job (UIUX-02), not this phase's. This phase only adds
  a small tick-status indicator (D-10), not the full view.

### Reviewed Todos (not folded)
None — discussion stayed within phase scope; no pending todos matched this
phase.

</deferred>

---

*Phase: 2-order-placing-tracking*
*Context gathered: 2026-09-30*
