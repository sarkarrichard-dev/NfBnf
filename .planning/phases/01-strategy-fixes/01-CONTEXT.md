# Phase 1: Strategy Fixes - Context

**Gathered:** 2026-09-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Raise the option buy lane's real (live-journal) win rate, focused first on
NIFTY, by finishing and tuning the OI-wall + fake-breakout entry work already
partially built — not by starting a new strategy or touching exits (Phase 3)
or the ML model itself. Every strategy-parameter change stays on the existing
confidence ladder; nothing here shortcuts it.

</domain>

<decisions>
## Implementation Decisions

### Which lever to pull first
- **D-01:** Finish and tune the OI-wall support/resistance + fake-breakout
  confirmation work already started 2026-09-12
  (`index_ai/strategies/buy_strategy.py`, `index_ai/strategies/breakout.py`),
  rather than starting a different approach from scratch.
- **D-02:** Focus the tuning effort on NIFTY first (worst current performer:
  20% win rate, -₹4,300 over 10 trades, and the most-traded index) — prove
  it works there before checking BankNifty/Sensex hold up on the same
  change.
- **D-03:** Fold the ML model's own flagged insight ("OI-aligned setups only
  win 33% — consider tightening REQUIRE_SUPERTREND_ALIGN or CPR gates") into
  this same tuning pass rather than treating it as a separate later attempt
  — both point at the same root cause (entries that aren't well-confirmed).
- **D-04:** Do **not** touch the buy-lane ML model itself this phase (its
  holdout accuracy is 36% on only 43 trades — too little data to meaningfully
  improve yet). Leave it scoring-only, let it keep accumulating data
  passively. Improving it is out of scope for Phase 1.
- **D-05:** All tuning stays paper-only until proven via the confidence
  ladder below — no live money on an unproven change, matching how every
  other strategy change in this project is handled.

### What counts as "fixed"
- **D-06:** Phase 1's target: **≥65% win rate AND net-positive rupees**,
  both measured over the same 40+ paper trades on the tuned NIFTY buy
  entries. Win rate alone is not sufficient — a high win rate that still
  loses money on net is not "fixed." — **Reversibility:** reversible (a
  target number, not a structural commitment; can be revisited if 40 trades
  in shows it was set wrong)
- **D-07:** 40+ trades is the sample size required before the 65%/net-positive
  numbers are trusted, matching the confidence-ladder threshold already used
  everywhere else in this project (not a smaller, easier-to-hit sample).

### How changes get proven before shipping
- **D-08:** Before any paper-trading change, run a real backtest using the
  recorded real option-chain data (`market_log.chain`, available since
  2026-09-23) as a first sanity check — even though that's currently only
  about a week of data and won't be conclusive on its own. It's cheap
  insurance against an obviously broken idea; the 40-trade paper ladder
  remains the real proof either way.
- **D-09:** Do not wait for more historical data to accumulate before running
  that backtest — run it now with what exists, understanding its limits.

### Scope of the confidence-ladder requirement
- **D-10:** STRAT-02 requires no new code. It's confirmation that the
  existing confidence-ladder discipline (~15 trades observe/log-only, ~40+
  trades human-approved suggestion, auto-apply only much later and never on
  a currently net-positive strategy) already covers this phase's changes.
  The plan must follow it, not build anything new for it.

### Claude's Discretion
- Exact new/adjusted gate parameters within the OI-wall/fake-breakout/
  REQUIRE_SUPERTREND_ALIGN/CPR-gate space are left to research + planning to
  work out — Richard specified the levers and the target, not the exact
  parameter values.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Standing project rules this phase must respect
- `memory/strategy-analysis-and-simplification-directive.md` — the
  confidence-ladder rules (15/40-trade gates, never auto-apply to a
  net-positive strategy), and the standing instruction that the buy lane
  should specifically use OI-profile S/R, candlestick patterns, and
  fake-breakout/breakdown detection
- `memory/strategy-findings.md` — prior measured buy-lane findings; the
  current baseline numbers referenced in this discussion come from the same
  family of live-journal measurements this file tracks
- `memory/new-strategy-bar-better-not-par.md` — the bar for any strategy
  change is genuinely better than what's running, not marginally close to
  par

### Existing partial work to finish, not replace
- `index_ai/strategies/buy_strategy.py` — `evaluate_buy_signal`, already
  wired to real option-chain OI walls (max put/call OI strikes)
- `index_ai/strategies/breakout.py` — `detect_breakout`, already requires
  `entry_confirmation_bars` consecutive closes beyond a level (the
  practical fake-breakout-detection mechanism)
- `index_ai/strategies/oi_credit.py` — the OI-profile S/R logic this reuses
  (originally sell-lane only, now also feeding the buy lane)

### Data and backtest tooling
- `index_ai/market_log.py` / `index_ai/strategy_lab.py` — real recorded
  option-chain data, available from 2026-09-23 onward; anything scored
  before that date used the Black-Scholes proxy and should not be treated as
  equivalent evidence
- `.planning/codebase/CONCERNS.md` — "Option chain data only from
  2026-09-23 onward" and "Strategy lab verdicts on thin sample size" concerns
  apply directly to how this phase's backtest and paper results should be
  read

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `index_ai/strategies/oi_credit.py`'s OI-profile support/resistance logic —
  already built for the sell lane, already partially reused by the buy lane;
  this phase extends that reuse rather than building new OI-wall detection
- `index_ai/strategies/breakout.py::detect_breakout` — the
  `entry_confirmation_bars` mechanism already implements the practical
  version of fake-breakout detection this phase is meant to tune, not
  reinvent

### Established Patterns
- The confidence ladder (`memory/strategy-analysis-and-simplification-directive.md`)
  is the only path any parameter change takes — 15 trades observe, 40+
  trades before a human-approved suggestion, no auto-apply on a net-positive
  strategy
- Real-money changes never happen before paper validation — established
  everywhere else in this codebase (crypto's own "live data only" rule,
  India's PAPER-by-default posture)

### Integration Points
- Strategy Lab (`index_ai/strategy_lab.py`) is the live proving ground this
  phase's paper trades will accumulate into and be scored by — its
  COLLECTING/PASSING/DROPPED verdict machinery already exists and should be
  reused, not duplicated

</code_context>

<specifics>
## Specific Ideas

No new mechanism requested beyond finishing what's already partially built —
Richard's direction was specifically to complete and tune the OI-wall +
fake-breakout work already started, combined with the ML model's own
already-surfaced gate-tightening suggestion, rather than to invent something
new.

</specifics>

<deferred>
## Deferred Ideas

- Improving the buy-lane ML model itself (more features, retraining
  approach) — explicitly deferred by Richard this phase (D-04); revisit
  once it has meaningfully more than 43 trades to learn from.
- BankNifty/Sensex-specific tuning — Phase 1 proves the approach on NIFTY
  first; extending/re-validating on the other two indices is implied
  follow-on work within this same phase's later plans, not a separate
  phase, but not the starting focus.

### Reviewed Todos (not folded)
None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-Strategy Fixes*
*Context gathered: 2026-09-30*
