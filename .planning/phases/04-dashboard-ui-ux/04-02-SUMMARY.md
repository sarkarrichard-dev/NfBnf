---
phase: 04-dashboard-ui-ux
plan: 02
subsystem: ui
tags: [data-health, fastapi, react, read-only, market-clock]

requires:
  - phase: 04-dashboard-ui-ux
    provides: "04-01 house style and dashboard build/test routine; server.py already hosts the live-pairs route"
provides:
  - "index_ai/data_health.py: classify, feed_status, chain_last_seen, spread_last_seen, collect (+ THRESHOLDS, OPEN_GRACE_SECONDS, CHAIN_LOOKBACK_SESSIONS, SPREAD_TAIL_BYTES)"
  - "GET /api/data-health (read-only, no params, async def + asyncio.to_thread)"
  - "DataHealthPanel: four StatTiles (Live prices, Option chain, Option spreads, Dhan live feed) at the top of the Index Options page"
  - "Header Ticks pill tooltips now point to the panel"
affects: [04-03-real-data-check]

actuals:
  tokens: 7000
  tasks: 3
  commits: 6

tech-stack:
  added: []
  patterns:
    - "Server decides every colour word (ok/slow/stale/closed/off/none) from its own clock and the NSE holiday calendar; TSX only maps word -> token"
    - "7 GB market log opened only through a mode=ro URI, seeks on the covering index idx_chain_session, bound parameters"
    - "Spread age = bounded 256 KB binary tail read of spread_samples.jsonl"

key-files:
  created:
    - index_ai/data_health.py
    - tests/test_data_health.py
    - dashboard/src/components/DataHealthPanel.tsx
  modified:
    - index_ai/server.py
    - dashboard/src/App.tsx
    - dashboard/src/components/shell/StatusPills.tsx

key-decisions:
  - "Verdict logic lives in Python and is pinned by pytest (no JS test runner); the browser never decides amber/red"
  - "Panel sits at the top of the Index Options page (header pills are hidden on phones and tied to sidebar offsets); the header Ticks pill points to it"
  - "Spread tail is 256 KB (not 64 KB) and each index steps back up to 3 sessions so one failing index shows its real age"
  - "Feed error text is never sent to the browser"

patterns-established:
  - "A read of the big database must be mode=ro and index-seek only; a missing file yields None and creates nothing"

requirements-completed: [UIUX-02, UIUX-03]

coverage:
  - id: D1
    description: "GET /api/data-health returns server-decided status words for prices, Dhan feed, option chain and spreads; never raises on missing files"
    requirement: UIUX-02
    verification:
      - kind: unit
        ref: "tests/test_data_health.py#test_classify_thresholds"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_tick_lines_follow_the_feed"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_data_health_endpoint"
        status: pass
    human_judgment: false
  - id: D2
    description: "Chain age from a read-only lookup and spread age from a file tail; nothing written, missing database not created"
    requirement: UIUX-02
    verification:
      - kind: unit
        ref: "tests/test_data_health.py#test_chain_reads_newest_rows_read_only"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_chain_missing_database_creates_nothing"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_spread_age_from_the_file_tail"
        status: pass
    human_judgment: false
  - id: D3
    description: "Market closed / holiday / square-off / open-bell never look red"
    requirement: UIUX-02
    verification:
      - kind: unit
        ref: "tests/test_data_health.py#test_closed_market_is_never_amber_or_red"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_square_off_pauses_the_chain_line"
        status: pass
      - kind: unit
        ref: "tests/test_data_health.py#test_open_bell_is_graced"
        status: pass
    human_judgment: false
  - id: D4
    description: "Panel placement, wording and colours on the Index Options page; header pill pointer"
    requirement: UIUX-03
    verification:
      - kind: other
        ref: "npm --prefix dashboard run build; tsc --noEmit -p tsconfig.app.json; eslint on DataHealthPanel, App, StatusPills; UIUX-03 greps"
        status: pass
    human_judgment: true
    rationale: "Layout, wording and the amber/red look need Richard's eye on the real page (end-of-phase look, plan 04-03); also placement assumption A5"

duration: 11min
completed: 2026-10-03
status: complete
---

# Phase 4 Plan 02: Data health panel Summary

**A "Data health" panel at the top of the Index Options page shows live-price age, option-chain age, spread age and the Dhan live-feed state, with every colour decided on the server from the market clock (amber/red only while the market is open) and the 7 GB market log read only through a read-only, index-seek lookup.**

## Performance

- **Duration:** about 11 min (20:10 to 20:21 IST)
- **Tasks:** 3 (1 tracer, 1 auto TDD, 1 auto)
- **Files:** 6 changed (3 new)

## Accomplishments

- `index_ai/data_health.py`: `classify` (D-03 rules incl. open-bell grace), `feed_status`, `chain_last_seen` (`mode=ro` URI, `MAX(session)` then `MAX(ts) WHERE session=? AND instrument=?`, three sessions deep), `spread_last_seen` (256 KB binary tail, first partial line dropped, junk lines skipped), `collect` (never raises, each line in its own try). The chain line is neutral during the 15:10-15:30 square-off window; the feed error text is never copied into the payload.
- `GET /api/data-health` right after `/api/tick-feed`: `async def` + `await asyncio.to_thread(collect)`, GET only (POST gives 405), no network call anywhere.
- `DataHealthPanel.tsx`: query `['data-health']`, 20 s poll, previous answer kept between polls, four `StatTile`s (2 columns on a phone, 4 on wider screens), per-index ages underneath, calm restart sentence if the endpoint is missing. Mounted between `<PageHeader>` and `<StatsOverview>` in the `trade` tab.
- Header Ticks pill: doc comment and every tooltip now point to "Data health, top of the Index Options page". Labels, tones, query and the other pills unchanged.

## Task Commits

1. **Task 1 RED:** `d20cd7d` (test) - 4 failing tests
2. **Task 1 GREEN (tracer):** `17086ed` (feat) - classify/feed/collect, endpoint, panel with price + feed tiles
3. **Task 2 RED:** `b7d21b9` (test) - 7 failing tests (6 new + extended endpoint test)
4. **Task 2 GREEN:** `02cbac8` (feat) - chain/spread lookups, rules, two more tiles, per-index lines
5. **Task 3:** `cf74a5d` (feat) - header pill pointer

Tracer gate: Task 1 `<verify>` re-run end to end (4 passed, build, tsc, eslint clean) before expansion.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug in my own test] missing-database test assumed an empty tmp directory**
- **Found during:** Task 2 (GREEN run)
- **Issue:** conftest already creates `.env` in `tmp_path`, so "directory listing is empty" was wrong.
- **Fix:** compare the directory listing before and after the call instead.
- **Files modified:** tests/test_data_health.py
- **Commit:** 02cbac8

**2. Plan wording only:** the `lineSub` helper does not take the whole payload (an unused parameter eslint would flag); signature is `lineSub(line, pausedForClose)`. No behaviour change.

**Total deviations:** 1 auto-fixed (test bug), 1 cosmetic. **Impact:** none on behaviour. The plan's "Deviations from 04-RESEARCH.md" (256 KB tail, 3-session lookback, inline query, no error text) were implemented as written.

## Real-data sanity run (read-only, one-off script, not a test)

Against the real `memory/market_log.sqlite` (7 GB) and `memory/spread_samples.jsonl`, on Saturday 20:18 IST:
- `chain_last_seen` took 0.022 s, `spread_last_seen` 0.0055 s, whole `collect()` 0.006 s on a second call. No error, DB file size and mtime unchanged (`-shm` mtime moved, normal for any reader of a WAL database; no new files).
- Result: market closed, so every line neutral ("closed"/"off"); chain last seen 03 Oct 2026 6:02 PM IST; spread last seen 01 Oct 2026 9:32 AM IST.
- **Worth Richard's attention (not caused by this plan):** the newest measured-spread sample is from Thursday morning (about 2.4 days ago), while the chain was recorded as late as Saturday 18:02. On a trading day the Option spreads tile will go red after 30 minutes if spreads are still not being recorded. That is exactly what the panel exists to show. Also the chain has a snapshot at 18:02 on a Saturday; the scanner or a script seems to record outside market hours (not investigated).

## Checklist findings (reviewers applied by hand; no subagent tool)

### ui-consistency-reviewer (7 checks + plain words) on DataHealthPanel.tsx, App.tsx, StatusPills.tsx

| # | Check | Result | Resolution |
|---|-------|--------|------------|
| 1 | Raw colours | Pass: only `slate`, `var(--up)`, `var(--warn)`, `var(--down)`; no hex/rgb/other palette utilities, no `var(--armed)` (stale is `--down`); added-line grep on App/StatusPills prints 0 | none needed |
| 2 | Corner radius | Pass: no `rounded-lg/xl/2xl/3xl`; panel via `fx.panel`, tiles via `StatTile` | none needed |
| 3 | Data mono + tabular-nums | Pass: tile values via `fx.cardValue` (mono, tabular), tile sub-lines tabular, the "Market open · checked ..." line and both per-index lines are `font-mono tabular-nums` | none needed |
| 4 | Hand-rolled panels | Pass: `cn(fx.panel, 'mb-5 p-4')`; no `bg-[var(--panel)]` | none needed |
| 5 | Local date logic | Pass: no `new Date(`, `toISOString(`, `getDay(`; ages are plain arithmetic on server seconds and the IST text is rendered as the server sent it (`istClock` in StatusPills is pre-existing and untouched) | none needed |
| 6 | Layout math | Pass: `git diff` against the plan commit for AppShell, TickerStrip, Sidebar is empty | none needed |
| 7 | Duplicate JSX | Pass: uses the shared `StatTile`; no table or tile clone | none needed |
| - | Plain words | Pass: no "websocket", "stalled", "readiness", "gate", "ladder" in the file; wording like "Running late", "Not updating", "Paused for the close" | none needed |

### trading-safety-reviewer

| Check | Result |
|-------|--------|
| 1 Blocking I/O on the event loop | Pass: handler is `async def` and the whole read (SQLite lookup, file tail) runs in `asyncio.to_thread(collect)`; chain lookups measured 0.02 s on the real 7 GB file; 2 s connect timeout |
| 2 Read-modify-write races | n/a: no writes, no delta endpoints, no new lock (lock count still 13) |
| 3 Live-arming interlock | n/a: nothing touches arming, modes or env flags |
| 4 Cost model | n/a: `spread_calib` is only read (file tail); no change to `charges.py` or measured-spread logic |
| 5 Order sequencing | n/a: no order code touched |
| Money-path diff | `git diff --quiet` against the plan commit for tick_feed, market_log, market_context/, scanner, executor, dhan_orders, exit, config, risk_manager, charges, crypto/, tests/conftest.py exits 0 |

### test-isolation-reviewer

| Check | Result |
|-------|--------|
| 1 Reaching `index_ai.notify` | Pass: tests call only the pure functions, `collect(now=...)` and `GET /api/data-health`; none reach `notify.*`; no Telegram variable set |
| 2 Real broker order | Pass: nothing in the call tree places an order or makes an HTTP call |
| 3 Real files | Pass: chain databases and spread files are `tmp_path` files; `market_log.DB_PATH` and `spread_calib.SAMPLES_PATH` are patched per test; no spread_calib writer is called so `SKIPS_PATH` is untouched; `tick_feed._state` and `ENABLE_TICK_FEED` are reset by an autouse fixture; all times are fixed IST datetimes (no weekday/clock dependence) |
| 4 conftest | Unchanged. Endpoint test uses a plain `TestClient(app)` (no `with`), so no background loops start |

## Verification run

- `python -m pytest tests/test_data_health.py tests/test_crypto_live_pairs.py tests/test_tick_feed.py -q`: 31 passed (10 in test_data_health). Full suite not run (orchestrator does).
- `npm --prefix dashboard run build`: succeeded. `npx tsc --noEmit -p tsconfig.app.json`: clean. `npx eslint` on DataHealthPanel.tsx, App.tsx, StatusPills.tsx: clean.
- Acceptance greps all pass: three defs; `asyncio.to_thread(collect)` count 1; `<PageHeader` < `<DataHealthPanel />` < `<StatsOverview` (233/238/239); `mode=ro` count 2, `market_log.connect` 0, `from ticks|count(` 0, `spread_calib.(summary|status)` 0; UIUX-03 greps all empty/0 as specified; pill pointer count 5.
- Lock tripwire: 13 (unchanged). Ruff `index_ai/`: Found 7 errors (same baseline files); new files and server.py pass cleanly.

## Issues Encountered

None. The PostToolUse hook printed transient tsc/ruff errors mid-way through multi-edit changes (import added in a later edit); the final results are clean.

## Known Stubs

None.

## Threat Flags

None. The one new endpoint is the one in the plan's threat model (GET-only, no parameters, inside the dashboard password gate).

## Next Phase Readiness

Ready for 04-03 (real-data check). Richard must restart the server once for `/api/data-health` to exist; until then the panel shows "Data health not available yet — the app may need a restart." `dashboard/dist` is rebuilt. Flagged assumptions to confirm at the end-of-phase look: panel placement on the Index Options page (A5), spread thresholds 600/1800 s (A1), open-bell grace 180 s (A2).

## Self-Check: PASSED

- FOUND: index_ai/data_health.py, tests/test_data_health.py, dashboard/src/components/DataHealthPanel.tsx
- FOUND commits: d20cd7d, 17086ed, b7d21b9, 02cbac8, cf74a5d
