---
phase: 04-dashboard-ui-ux
plan: 01
subsystem: ui
tags: [crypto, fastapi, react, live-vs-paper, read-only]

requires:
  - phase: 03-exit-optimisation
    provides: house dashboard style (ExitRecheckPanel), 13-lock tripwire, ruff baseline of 7
provides:
  - "crypto_live_pair_view(active, armed): the lane's own live rule (crypto/lanes.py:599) applied to the pairs the lane really visits, with plain-word reasons"
  - "GET /api/crypto/live-pairs (read-only, async def + asyncio.to_thread, no params)"
  - "CryptoLivePairs: grouped LIVE/PAPER list directly under the arm strip in the Crypto tab"
affects: [04-02-data-health, 04-03-real-data-check]

actuals:
  tokens: 12000
  tasks: 2
  commits: 3

tech-stack:
  added: []
  patterns:
    - "Server decides live vs paper and writes the reason; TSX only renders it"
    - "Query key ['crypto', 'live-pairs'] sits under ['crypto'] so arm/disarm/mode mutations refresh it at once"

key-files:
  created:
    - dashboard/src/components/CryptoLivePairs.tsx
  modified:
    - index_ai/strategy_performance.py
    - index_ai/server.py
    - tests/test_crypto_live_pairs.py
    - dashboard/src/components/CryptoExecutionPanel.tsx
    - dashboard/src/components/CryptoPanel.tsx

key-decisions:
  - "Old Go-live readiness block in CryptoPanel is replaced (deleted), not kept beside the new list; /api/crypto/live-readiness stays on the server with no caller"
  - "LIVE chips use the reserved real-orders red var(--armed), shown only when crypto is armed"
  - "armed comes from the server (crypto_settings().live_orders_enabled), never from the status endpoint's live_armed"

patterns-established:
  - "View over the lane's own helpers (_enabled_strategies x _symbols_for) instead of re-deriving the pair set"

requirements-completed: [UIUX-01, UIUX-03]

coverage:
  - id: D1
    description: "GET /api/crypto/live-pairs lists exactly the pairs the lane visits and marks live = armed and eligible"
    requirement: UIUX-01
    verification:
      - kind: unit
        ref: "tests/test_crypto_live_pairs.py#test_live_pairs_endpoint_mirrors_the_lane"
        status: pass
      - kind: unit
        ref: "tests/test_crypto_live_pairs.py#test_view_lists_only_the_pairs_the_lane_visits"
        status: pass
      - kind: unit
        ref: "tests/test_crypto_live_pairs.py#test_view_live_only_when_armed"
        status: pass
    human_judgment: false
  - id: D2
    description: "Plain-words reasons for every paper pair; unreadable journal keeps everything paper"
    requirement: UIUX-01
    verification:
      - kind: unit
        ref: "tests/test_crypto_live_pairs.py#test_view_reasons_are_plain_words"
        status: pass
      - kind: unit
        ref: "tests/test_crypto_live_pairs.py#test_view_unreadable_list_keeps_everything_paper"
        status: pass
    human_judgment: false
  - id: D3
    description: "Crypto tab shows the grouped list right under the Paper/Live switch and arm strip; old go-live block removed"
    requirement: UIUX-03
    verification:
      - kind: other
        ref: "npm --prefix dashboard run build; tsc --noEmit -p tsconfig.app.json; eslint on the three touched files"
        status: pass
    human_judgment: true
    rationale: "Layout, wording and the red LIVE chip colour need Richard's eye on the real tab (end-of-phase look, plan 04-03)"

duration: 25min
completed: 2026-10-03
status: complete
---

# Phase 4 Plan 01: Live vs paper crypto pairs Summary

**After arming crypto, the Crypto tab now lists, right under the arm button, exactly which (strategy, coin) pairs use real money and which stay on paper with a plain reason, using the same rule the trading lane uses (live = armed and pair in crypto_live_pairs()).**

## Performance

- **Duration:** about 25 min
- **Completed:** 2026-10-03
- **Tasks:** 2 (1 tracer, 1 auto)
- **Files modified:** 7 (6 from the plan plus this summary's tracking files)

## Accomplishments

- `crypto_live_pair_view(active, armed)` in `index_ai/strategy_performance.py`, next to `crypto_live_pair_table` (which is unchanged). It emits only the pairs the lane visits, so retired coins (PAXGUSD, XAUTUSD), coins a strategy is limited away from, and the paper-only `btc_daily_straddle` never show. Reasons are built from the module constants, with proper plurals and `-$18.00` money format. An unreadable journal gives `read_ok false` and everything paper, as the lane itself does.
- `GET /api/crypto/live-pairs` computes `active` with the lane's own two calls (`_enabled_strategies`, `_symbols_for`) and `armed` as `crypto_settings().live_orders_enabled`, all inside `asyncio.to_thread`. GET only (POST gives 405).
- `CryptoLivePairs.tsx`: heading switches between "Right now: which pairs use real money" and "If you arm: which pairs would use real money", a count line, one plain sentence (including that open trades keep the mode they opened with), pairs grouped per strategy with LIVE/PAPER chips, shared reasons printed once, per-coin lines with trades and net P&L when they differ.
- Old `LiveReadinessRow`, its `readiness` query and its two types removed from `CryptoPanel.tsx` (109 lines deleted), so there is one list, not two.

## Task Commits

1. **Task 1 RED:** `7ad18e9` (test) - five failing tests
2. **Task 1 GREEN (tracer):** `9de2e14` (feat) - view function, endpoint, flat list mounted under the arm strip
3. **Task 2:** `f91bd92` (feat) - grouped list, old block removed

Tracer gate: `<verify>` re-run end to end after Task 1 (7 passed, build, tsc, eslint clean) before expansion.

**Plan metadata:** see the final docs commit.

## Files Created/Modified

- `index_ai/strategy_performance.py` - `_usd_text`, `_paper_reason`, `crypto_live_pair_view` (additive; existing functions byte-for-byte unchanged)
- `index_ai/server.py` - `GET /api/crypto/live-pairs`
- `tests/test_crypto_live_pairs.py` - 5 new tests
- `dashboard/src/components/CryptoLivePairs.tsx` - new list component
- `dashboard/src/components/CryptoExecutionPanel.tsx` - import + `<CryptoLivePairs />` after the arm strip, before the kill-switch list
- `dashboard/src/components/CryptoPanel.tsx` - old go-live block removed

## Decisions Made

- Followed the plan. The three "flagged assumptions" in the plan stand and should be told to Richard in one plain sentence at the end-of-phase look: the old Go-live block is replaced; LIVE chips use the real-orders red (one-class change to green if he prefers); off-by-default strategies (bb_reversal, ema_jaguar, vp_edge) show their id with spaces.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] "1 trades" in the per-coin tail**
- **Found during:** Task 2
- **Issue:** The plan's tail text "· {trades} trades ·" would print "1 trades" for a single trade, which the plan itself bans in reasons (D-04 plain words).
- **Fix:** `{p.trades === 1 ? 'trade' : 'trades'}`.
- **Files modified:** dashboard/src/components/CryptoLivePairs.tsx
- **Commit:** f91bd92

**Total deviations:** 1 auto-fixed (1 bug). **Impact:** wording only.

Note: the `strategy_performance.py` formatter hook reflowed my additions slightly (84 added lines, 0 removed); no existing line changed.

## Checklist findings (reviewers applied by hand; no subagent tool)

### ui-consistency-reviewer (7 checks + plain words) on CryptoLivePairs.tsx, CryptoExecutionPanel.tsx, CryptoPanel.tsx

| # | Check | Result | Resolution |
|---|-------|--------|------------|
| 1 | Raw colours | Pass: only `slate`, `var(--armed)`, `var(--warn)`, `var(--up)`/`--down` (via `pnlCls`), `bg-white/[0.05]` (already used in the repo); no hex/rgb or other palette utilities (grep prints nothing) | none needed |
| 2 | Corner radius | Pass: chips use small `rounded`; no `rounded-lg/xl/2xl/3xl` in new lines. The old `rounded-lg` tiles left with `LiveReadinessRow` | none needed |
| 3 | Data mono + tabular-nums | Pass: count line, trades and P&L tail are `font-mono tabular-nums`; chips and coin names are `font-mono` | none needed |
| 4 | Hand-rolled panels | Pass: no `bg-[var(--panel)]`; the list is a plain block with a top hairline inside the existing `fx.panel` of CryptoExecutionPanel | none needed |
| 5 | Local date logic | Pass: no `new Date(`, `toISOString(`, `getDay(`; the component does no date work | none needed |
| 6 | Layout math | Pass: `git diff` against the plan commit for `components/shell/` is empty | none needed |
| 7 | Duplicate JSX | Pass: old `LiveReadinessRow` deleted rather than left as a twin; list uses `STRATEGIES`, `usd`, `pnlCls` helpers; no stat-tile or table clone | none needed |
| - | Plain words | Pass: no "readiness", "gate", "ladder", "websocket", "stalled" in the file (grep prints nothing); strategy names via `STRATEGIES` | none needed |

### trading-safety-reviewer

| Check | Result |
|-------|--------|
| 1 Blocking I/O on the event loop | Pass: handler is `async def` and the whole read (settings, journal) runs in `asyncio.to_thread(_read)` |
| 2 Read-modify-write races | n/a: no writes at all; no delta endpoints |
| 3 Live-arming interlock | Pass: arming code untouched; `armed` is `live_orders_enabled` (mode LIVE and armed and keys), so Paper mode, missing keys or a disarmed switch show nothing live |
| 4 Cost model | n/a: no change to charges, spread_calib or P&L maths |
| 5 Order sequencing | n/a: no order code touched |
| Money-path diff | `git diff --quiet` against the plan commit for `crypto/`, `executor.py`, `dhan_orders.py`, `exit.py`, `index_ai/config.py`, `risk_manager.py`, `charges.py` exits 0 |

### test-isolation-reviewer

| Check | Result |
|-------|--------|
| 1 Reaching `index_ai.notify` | Pass: tests call only the pure view, `crypto_live_pairs()` and `GET /api/crypto/live-pairs`; none of these reach `notify.*`; no Telegram variable is set anywhere |
| 2 Real broker order | Pass: nothing in the call tree places an order; the endpoint test replaces `crypto.config.crypto_settings` with a fake settings object |
| 3 Real files | Pass: every test calls `_journal(...)`, which patches `crypto.journal.JOURNAL_PATH` to `tmp_path` and `data_epoch` to None; the real `memory/crypto_journal.jsonl` is never read |
| 4 conftest | Unchanged. The endpoint test uses a plain `TestClient(app)` (no `with` block), so no background loops start |

## Verification run

- `python -m pytest tests/test_crypto_live_pairs.py -q`: 7 passed (2 existing + 5 new). Also `tests/test_crypto_phase4.py` and `tests/test_strategy_performance.py`: 28 passed total with the live-pairs file. Full suite not run (orchestrator does).
- `npm --prefix dashboard run build`: succeeded. `npx tsc --noEmit -p tsconfig.app.json`: clean. `npx eslint` on CryptoLivePairs, CryptoPanel, CryptoExecutionPanel: clean.
- Acceptance greps: all pass (def count 3; endpoint grep count 3; money-path diff clean; Arm live orders < `<CryptoLivePairs />` < Kill switch order at lines 262/268/290; query key present; UIUX-03 checks 1-7 as above).
- Lock tripwire: 13 (unchanged). Ruff `index_ai/`: Found 7 errors, same files as baseline, none in files touched.

## Issues Encountered

None. The PostToolUse hook printed transient `Cannot find name 'CryptoLivePairs'` from tsc mid-way through the two-edit mount; the final result is clean.

## Known Stubs

None.

## Threat Flags

None. The one new endpoint is the one in the plan's threat model (GET-only, no params, inside the dashboard password gate).

## Next Phase Readiness

Ready for 04-02 (Data Health). The server needs one restart by Richard before `/api/crypto/live-pairs` exists; until then the list shows "not available yet - the app may need a restart". `dashboard/dist` is rebuilt.

## Self-Check: PASSED

- FOUND: dashboard/src/components/CryptoLivePairs.tsx
- FOUND commits: 7ad18e9, 9de2e14, f91bd92
