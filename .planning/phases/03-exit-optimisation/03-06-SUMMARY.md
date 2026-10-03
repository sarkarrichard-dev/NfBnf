---
phase: 03-exit-optimisation
plan: 06
subsystem: ui
tags: [react, tanstack-query, dashboard, exit-recheck, strategy-pnl]

requires:
  - phase: 03-exit-optimisation
    provides: "03-01/04/05 GET /api/exit-recheck and POST /api/exit-recheck/run, row shape incl. drift / drift_detail"
provides:
  - "useExitRecheck (60 s poll) and useRunExitRecheck (POST, no body) hooks with ExitRecheck / ExitRecheckRow types"
  - "Stop check panel on the Strategy P&L tab: every segment, Re-check now button, 'results changed' flag, suggestion line, plain footer"
  - "index_options engine text names the 1:1 index-point trail (EXIT-02)"
  - "CONCERNS.md records that trail monitoring now exists"
affects: [phase-3 verification, end-of-phase manual look]

actuals:
  tokens: 2200
  tasks: 2
  commits: 2

tech-stack:
  added: []
  patterns:
    - "Panel copies LearningPanel markup (fx.panel, STATE_STYLE pills, fmt/pct helpers); mutation copies FeaturesPanel (sonner toast, invalidate, Button pending)"
    - "Missing endpoint renders a calm sentence, not an error box: isError with no cached data"

key-files:
  created:
    - dashboard/src/hooks/useExitRecheck.ts
  modified:
    - dashboard/src/components/pages/StrategyPerformancePage.tsx
    - dashboard/src/lib/strategies.ts
    - .planning/codebase/CONCERNS.md

key-decisions:
  - "Was-to-now line is shown only when a row is flagged (row.drift), not whenever drift_detail exists: the backend fills drift_detail for every ready segment that has a baseline, so gating on it alone would print the line on healthy rows"
  - "lane typed 'buy' | 'sell' | 'all' and distance left out of the type, because the real rows use 'all' for crypto/commodities and an object distance for commodities"

requirements-completed: [EXIT-01, EXIT-02, EXIT-03]

duration: 12min
completed: 2026-10-03
status: complete
---

# Phase 3 Plan 06: Stop check panel Summary

**Strategy P&L "Stop check" panel: every segment's stop, state, win rate, share closed by the stop and net, a Re-check now button, and a red "results changed" flag, display only (no control changes a stop).**

## Performance

- **Duration:** ~12 min
- **Completed:** 2026-10-03
- **Tasks:** 2
- **Files modified:** 4 (1 new)

## Accomplishments
- New hook file with the query (60 s poll, keepPreviousData) and the POST mutation (no body, toast "Stop check done", refresh); types match the real stored row, including the 'all' lane and the internal keys the UI ignores.
- ExitRecheckPanel mounted between RiskManagerPanel and LearningPanel. Shows header (last run IST string, "n changed" pill, Re-check now button with pending spinner), one block per segment, "Could not read: ..." line for errors, and the footer saying nothing here changes a stop.
- States handled: loading ("Loading..."), failed GET with nothing cached ("The stop check is not available yet — the app may need a restart."), never run / empty segments ("Not run yet — press Re-check now..."), null win rate / stop-closed rate render as a dash.
- Last "premium-trail" wording removed from the dashboard; CONCERNS.md gets the monitoring note.

## Task Commits

1. **Task 1: Stop check panel** - `8513232` (feat)
2. **Task 2: stale wording + CONCERNS note** - `2d56496` (chore)

**Plan metadata:** see the docs(03-06) commit that follows.

## Files Created/Modified
- `dashboard/src/hooks/useExitRecheck.ts` - query + mutation hooks and types
- `dashboard/src/components/pages/StrategyPerformancePage.tsx` - ExitRecheckPanel and its mount
- `dashboard/src/lib/strategies.ts` - one-line engine wording (EXIT-02)
- `.planning/codebase/CONCERNS.md` - one monitoring line under the trailing-stop item

## ui-consistency-reviewer checklist (applied by hand, no subagent available)

| # | Check | Finding | Resolution |
|---|-------|---------|------------|
| 1 | Raw colours vs tokens | None. Only slate-*, var(--down), var(--warn), var(--up), var(--hair); grep for hex / rgb( on the hook and on the added page lines prints nothing | none needed |
| 2 | Corner-radius drift | None added. Panel is fx.panel; pills use the same `rounded` as LearningPanel. The Button's own rounded-lg is the shared primitive's, pre-existing | none needed |
| 3 | Data as mono / tabular-nums | Stats line and was-to-now line are font-mono tabular-nums. Header "last run" time and pills are font-mono without tabular-nums, same as LearningPanel's epoch line; mono is fixed-width so digits already align | accepted, matches neighbour |
| 4 | Hand-rolled panel | None: `cn(fx.panel, 'p-4')` | none needed |
| 5 | Local Date/UTC logic | None: `ran_at.slice(0, 16).replace('T', ' ')` renders the server's IST string; no `new Date(` in the hook or in added lines | none needed |
| 6 | Layout math (header/ticker/sidebar) | Not touched | n/a |
| 7 | Duplicated shared component | Reuses Button, STATE_STYLE, fmt, pct. TradeLogTable / StatTile / PeriodBar / EquityCurve / PnlCalendar do not fit a per-segment text list (LearningPanel is the sibling pattern) | none needed |

Wording check: grep of added lines for baseline / ladder / `pp` finds none in visible text; "drift" appears only in identifiers (`r.drift`, `drift_detail`), never on screen. Visible words are "Stop check", "results changed", "Re-check now", "not run yet", "last run".

## Decisions Made
See key-decisions above. The one-button-only rule (T-03-24) holds: the only handler is `run.mutate()`, no inputs, the POST has no body, and a suggestion is a text line in the `--warn` colour.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Was-to-now line gated on `drift`, not just `drift_detail`**
- **Found during:** Task 1 (reading the real `_track_drift` in index_ai/exit_recheck.py)
- **Issue:** The plan says to show the was-to-now line "when drift_detail exists". The backend sets `drift_detail` on every ready segment that has a baseline, flagged or not, so the red line would show on healthy segments.
- **Fix:** Show it only when `r.drift && r.drift_detail`.
- **Files modified:** dashboard/src/components/pages/StrategyPerformancePage.tsx
- **Committed in:** 8513232

**2. [Plan wording] `lane` allows 'all'; `distance` omitted from the type** - the plan's interface block said lane is 'buy' | 'sell' | null; the real rows use 'all' for crypto and commodities. Followed the real shape (orchestrator note).

**Total deviations:** 1 auto-fixed (Rule 1), 1 type-shape correction. **Impact:** none on scope; no backend change.

## Issues Encountered
- The post-edit hook printed tsc errors in the middle of the three-edit sequence on StrategyPerformancePage.tsx (import added last); they cleared once all edits were in. Final tsc is clean.
- No JS test runner and no running server was used (per instructions), so the panel was checked against the real row shape by reading `_segment_row` / `_track_drift` / `last_result`, not by loading it. The end-of-phase human look still applies.

## Verification
- `npm --prefix dashboard run build` exit 0 (dashboard/dist freshly built; gitignored)
- `npx tsc --noEmit -p tsconfig.app.json` exit 0
- `npx eslint` on the two touched files: exit 0, no output
- Acceptance greps: ExitRecheckPanel defined (line 110) and mounted (line 580) between RiskManagerPanel and LearningPanel; both api paths match in the hook; colour/radius/Date grep empty
- Task 2 verify command prints `ok`; `git diff --stat` on strategies.ts is a 1-line change
- Lock tripwire (`threading.Lock()` / `RLock()` count) = 13 (unchanged); `ruff check index_ai/` = 7 errors (unchanged baseline)

## Known Stubs
None. Empty and null cases render real text, not placeholders.

## Threat Flags
None. No new endpoint, no input, no body; the existing POST is already inside the password gate.

## User Setup Required
None. Richard must restart the server once to get the new endpoints; until then the panel shows "The stop check is not available yet — the app may need a restart."

## Next Phase Readiness
Phase 3 plans complete; ready for the phase gate (full pytest, reviewer agents, and the human look at the Strategy P&L tab with the server running).

## Self-Check: PASSED
- hook file, panel, strategies.ts, CONCERNS.md edits present; commits 8513232 and 2d56496 exist
