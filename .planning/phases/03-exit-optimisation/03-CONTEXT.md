# Phase 3: Exit Optimisation - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Keep trailing-stop tuning current against live-journal data instead of frozen
at the one-time 2026-09-24/28 hand tune, and leave the codebase with exactly
one trailing-stop implementation. Three deliverables, nothing more: (1) a
repeatable re-check of each market's current stop distances against recent
live trades (EXIT-01), (2) removal of the old percent-of-option-price trail,
`index_ai/premium_trail.py` (EXIT-02), (3) a drift warning when a stop's
hit-rate or win-rate moves away from its last check (EXIT-03). This phase
does not change any stop distance by itself and does not touch entry logic,
strategies, or order placement.

</domain>

<decisions>
## Implementation Decisions

### Scope
- **D-01:** The re-check and drift warning cover **every segment** — India
  options (NIFTY/BANKNIFTY/SENSEX, buy and sell lanes), crypto (the 1.6%
  `point_trail_pct` trail in `crypto/strategies/trailing.py`) and MCX
  commodities. Richard chose "Everything" over the roadmap's India-only
  wording. Segments with thin data simply report "not enough data yet" (see
  D-03) rather than being excluded. — **Reversibility:** reversible (scope of a
  read-only report).

### What the re-check does
- **D-02:** When fresh trades say a stop distance looks wrong, the system
  **suggests a better number for Richard to approve** (plain-language, in
  rupees and win-rate points: "NIFTY buy 25-pt stop looks too tight — 30
  would have kept ₹X more over the last N trades"). It never changes a stop
  by itself. Same confidence-ladder posture as Phase 1 and the standing ML
  guardrails: suggest only, human approves, never auto-apply.
- **D-03:** A segment needs **40+ trades and 14+ trading days** before the
  system may say a stop looks wrong or suggest a change; below that it says
  "not enough data yet". Same bar used everywhere else in the project.

### Removing the old trail
- **D-04:** Delete `index_ai/premium_trail.py` fully, including its tests
  (`tests/test_premium_trail.py`) and every import/branch that references it
  (`index_ai/trailing.py`, `index_ai/position_exits.py`, comment in
  `entry_guard.py`). **First copy its useful history** (the 2026-09-23
  "5% first target, 25% almost never armed, replay of 37 real MTM paths"
  findings and the per-index config reasoning) into
  `memory/strategy-findings.md` so the lesson survives the deletion.
  **Before deleting, confirm whether any live trade can still reach it** —
  initial scouting shows `trailing.py` only runs it for non-long-premium
  positions on NIFTY/BANKNIFTY/SENSEX, and credit spreads run their own index
  trail in `credit_spread.py`, so it looks unreachable, but research must prove
  that rather than assume it. — **Reversibility:** reversible via git history.
- **D-05:** The point-based index trail (stop follows the index 1:1) **counts
  as both the trailing stop and the trailing profit** for Richard's standing
  "every segment needs both" rule. Do not add a second profit mechanism. The
  rupee `profit_trail` fallback used when no option quote exists stays as it is
  — this phase does not remove it.

### Drift warning
- **D-06:** "Drifting" means a stop's hit-rate or win-rate moves **more than
  15 percentage points** from its last-check baseline, and only once the
  segment has 40+ trades (D-03). Avoids alarms from a couple of unlucky trades.
- **D-07:** A drift warning goes out as a **Telegram message plus a visible
  flag on the dashboard** (Strategy P&L tab). One message per drift event, not
  repeated every scan (reuse the existing de-duplicating `notify.alert` key
  pattern).
- **D-08:** The re-check runs **automatically once a day after the market
  closes, plus a dashboard button to run it any time.** Crypto has no market
  close, so its daily run uses the same end-of-day time as India — exact time
  is Claude's discretion.

### Claude's Discretion
- Exact end-of-day run time (D-08), and where the baseline ("last check")
  numbers are stored, as long as they survive a server restart.
- Exact wording and placement of the dashboard flag and the "Re-check now"
  button, reusing existing Strategy P&L tab patterns and shared UI primitives.
- How exit reasons are read back out of the journal for the hit-rate numbers
  (initial scouting found exit reasons are not under the obvious
  `exit_reason` keys in `option_json`; research must find where they actually
  live and whether every segment records them consistently).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/ROADMAP.md` — Phase 3 goal and success criteria
- `.planning/REQUIREMENTS.md` — EXIT-01, EXIT-02, EXIT-03

### Current stop rules (the numbers being re-checked)
- `CLAUDE.md` — "Trailing stops are now index-point-based" section: buys
  NIFTY 25 / BANKNIFTY 55 / SENSEX 80 (`instruments._buy_scalp_trail`),
  sells NIFTY 40 / BANKNIFTY 100 / SENSEX 130
  (`credit_spread.SELL_TRAIL_POINTS`); crypto 1.6% `point_trail_pct`
- `index_ai/instruments.py`, `index_ai/strategies/credit_spread.py`,
  `index_ai/trailing.py`, `index_ai/position_exits.py` — where those rules run
- `index_ai/premium_trail.py` — the thing to delete; its docstring holds the
  history to preserve
- `crypto/strategies/trailing.py`, `commodities/` — the other two segments' trails

### Standing rules this phase must respect
- `memory/strategy-analysis-and-simplification-directive.md` — confidence
  ladder (suggest only, human approves, never auto-apply; live data only)
- `memory/strategy-findings.md` — destination for the preserved premium-trail
  history (D-04)
- Standing rule (memory: standard-trailing-stop-and-profit) — every segment
  keeps a trailing stop and a trailing profit; D-05 records how that is met
- `CLAUDE.md` money-path section — `trailing.py` and the stop logic are on the
  caution list; the trading-safety-reviewer applies

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `index_ai/strategy_performance.py` `strategy_scorecard()` — per-(strategy,
  instrument) live-journal numbers, now with a `since=` cut-off (Phase 1)
- `index_ai/strategy_learning.py` — the 15/40-trade confidence ladder
  (watching/observing/ready/frozen) the suggest-only flow should reuse, not
  duplicate
- `index_ai/notify.py` `alert(text, key=...)` — de-duplicated Telegram alert
  (used by Phase 2's reconcile alerts)
- Strategy P&L dashboard tab — natural home for the drift flag and button
- `index_ai/strategy_lab.py` replay machinery — already replays real chain
  data with the live 1:1 trail distances (`BUY_TRAIL_POINTS`,
  `SELL_TRAIL_POINTS`); candidate engine for "what would a different distance
  have done"

### Established Patterns
- Suggest-only, human-approved, nothing auto-applied; "not enough data yet"
  below 40 trades / 14 days
- Daily end-of-day jobs already exist for India (`eod_report` in the scanner)
  and for crypto's nightly tasks — the daily re-check should hook the same way

### Integration Points
- Exit-reason data in the journal (location unconfirmed, see Claude's
  Discretion) — the hit-rate numbers depend on it
- Scorecard/Strategy P&L API for the dashboard flag

</code_context>

<specifics>
## Specific Ideas

No specific implementation given — Richard's input was on scope (everything),
behaviour (suggest only, 40 trades / 14 days, 15-point drift, Telegram plus
dashboard, daily plus button) and cleanup (delete fully, keep the history).

</specifics>

<deferred>
## Deferred Ideas

- Automatically applying a suggested stop distance — explicitly ruled out;
  only ever a human-approved change.
- Removing the rupee `profit_trail` fallback — kept as-is by D-05, not this
  phase.

### Reviewed Todos (not folded)
None — no pending todos matched this phase.

</deferred>

---

*Phase: 3-Exit Optimisation*
*Context gathered: 2026-10-03*
