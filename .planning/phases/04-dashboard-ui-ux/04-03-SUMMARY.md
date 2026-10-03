---
phase: 04-dashboard-ui-ux
plan: 03
subsystem: verification
tags: [phase-gate, real-data-check, read-only, safety-tripwires, ui-consistency]

requires:
  - phase: 04-dashboard-ui-ux
    provides: "04-01 crypto live/paper list (GET /api/crypto/live-pairs) and 04-02 Data health panel (GET /api/data-health)"
provides:
  - "Both new read paths proven on the real memory/ data without a write (sub-0.03 s each)"
  - "Money-path / data-writer files proven untouched since the plan commit; ruff 7, locks 13, full pytest run recorded"
  - "CONCERNS.md: the two Phase 4 gaps marked resolved"
  - "Whole-phase visual-bar table and the end-of-phase look for Richard (PENDING)"
affects: [verify-work-phase-04]

actuals:
  tokens: 200
  tasks: 2
  commits: 1

key-files:
  created: []
  modified:
    - .planning/codebase/CONCERNS.md

key-decisions:
  - "No dashboard change was needed: the whole-phase checklist found nothing, so Task 2 produced no code commit (build, tsc, eslint re-run clean)"

requirements-completed: []
requirements-pending-human: [UIUX-01, UIUX-02, UIUX-03]

coverage:
  - id: D1
    description: "data_health.collect() and crypto_live_pair_view run read-only against the real memory/ files, fast, with valid status words and a clean unarmed pair list"
    requirement: UIUX-01
    verification:
      - kind: other
        ref: "throwaway real-data script (scratchpad, freeze_env first, mode=ro) - printout below"
        status: pass
    human_judgment: false
  - id: D2
    description: "Nothing on the money path or data-writer side changed over the phase; ruff 7; locks 13; routes GET only"
    requirement: UIUX-03
    verification:
      - kind: other
        ref: "git diff --name-only 9f9a910 over money-path files = empty; ruff Found 7 errors.; lock grep 13; two @app.get routes"
        status: pass
    human_judgment: false
  - id: D3
    description: "Full pytest suite: 852 passed, 1 failed (known weekend-only commodities test)"
    requirement: UIUX-03
    verification:
      - kind: other
        ref: "python -m pytest -q"
        status: pass
    human_judgment: false
  - id: D4
    description: "Whole-phase dashboard diff against the 7 ui-consistency checks plus plain words; and how the real page looks and reads"
    requirement: UIUX-03
    verification:
      - kind: other
        ref: "npm --prefix dashboard run build; tsc --noEmit -p tsconfig.app.json; eslint on the 6 touched files; the plan's three greps"
        status: pass
    human_judgment: true
    rationale: "Closed-market neutrality, list placement under the Paper/Live switch, phone width and the A5 placement question need Richard's eye on the real dashboard (human-check below, PENDING)"

duration: 7min
completed: 2026-10-03
status: complete
---

# Phase 4 Plan 03: Phase gate Summary

**Both new Phase 4 views were run read-only against Richard's real data (0.024 s and 0.027 s), nothing on the money path moved since the plan commit, the full suite is green apart from the known weekend-only commodities test, and the two codebase concerns are marked resolved; Richard's own look at the real dashboard is still PENDING.**

## Performance

- **Duration:** about 7 min (14:55 to 15:02 UTC)
- **Tasks:** 2 (both auto)
- **Files:** 1 changed (`.planning/codebase/CONCERNS.md`)

## Task Commits

1. **Task 1:** `b8b6aca` (docs) - CONCERNS.md: two "Resolved 2026-10 (Phase 4)" lines. The real-data check, tripwires and full suite produced no repo changes beyond this.
2. **Task 2:** no code commit - the checklist found nothing to fix, so `CryptoLivePairs.tsx` / `DataHealthPanel.tsx` are unchanged; the rebuild, tsc and eslint were re-run clean.

## Real-data check (read only, 2026-10-03 about 20:25 IST, Saturday, market closed)

Script lived in the session scratchpad (outside the repo). It called `index_ai.config.freeze_env()` first, so `.env` was never loaded and default settings applied. Nothing armed, nothing written, no Dhan/Delta call. Server not started.

```
data_health.collect elapsed=0.024s
as_of_display: 03 Oct 2026, 8:25:53 PM IST market_open: False square_off_window: False
  ticks: status=off age_seconds=None last_seen=None
  feed: status=off age_seconds=None last_seen=None
  chain: status=closed age_seconds=8611 last_seen=03 Oct 2026, 6:02:22 PM IST
     by_index = {'NIFTY': 8611, 'BANKNIFTY': 8597, 'SENSEX': 8579}
  spread: status=closed age_seconds=212009 last_seen=01 Oct 2026, 9:32:24 AM IST
     by_index = {'NIFTY': 212009, 'BANKNIFTY': 212003, 'SENSEX': 211998}
crypto_live_pair_view elapsed=0.027s armed=False read_ok=True pairs=23
  ak_roxx_pro   BNBUSD  trades=9  eligible=False  losing on this coin so far (-$15.87)
  ak_roxx_pro   BTCUSD  trades=15 eligible=True   ready: new trades use real money once crypto is armed
  ak_roxx_pro   DOGEUSD trades=1  eligible=False  only 1 trade on this coin so far (needs 5)
  ak_roxx_pro   ETHUSD  trades=13 eligible=True   ready: ...
  ak_roxx_pro   SOLUSD  trades=7  eligible=True   ready: ...
  ak_roxx_pro   XRPUSD  trades=6  eligible=True   ready: ...
  cpr_trend     BNBUSD  trades=11 eligible=True   ready: ...
  cpr_trend     BTCUSD  trades=14 eligible=False  losing on this coin so far (-$33.27)
  cpr_trend     DOGEUSD trades=4  eligible=False  only 4 trades on this coin so far (needs 5)
  cpr_trend     ETHUSD  trades=15 eligible=True   ready: ...
  cpr_trend     SOLUSD  trades=13 eligible=True   ready: ...
  cpr_trend     XRPUSD  trades=15 eligible=True   ready: ...
  ichimoku      6 coins (0-2 trades each)  eligible=False  this strategy needs 30 trades first (12 so far)
  ny_n_break    BTCUSD  trades=12 eligible=False  this strategy is losing overall so far (-$143.70)
  ny_n_break    ETHUSD  trades=8  eligible=False  this strategy is losing overall so far (-$143.70)
  rsi_adx_trend BTCUSD/ETHUSD/SOLUSD  eligible=False  this strategy needs 30 trades first (13 so far)
eligible count: 8
ALL ASSERTIONS PASSED
```

All assertions held: elapsed under 2 s for both; every block status in `STATUSES`; no `slow`/`stale` while closed (D-03); `read_ok` true; no pair live while unarmed; no `btc_daily_straddle`; no PAXGUSD/XAUTUSD; no reason containing "None", "$-" or " 1 trades" (D-01).

Notes on the printout, not problems: the live-price and Dhan-feed lines read `off` only because the check ran with the frozen default settings (no `.env`, so `ENABLE_TICK_FEED` is its default false); Richard's running server will show its real feed state. The spread file's newest sample is Thursday 1 Oct 09:32 IST (Friday 2 Oct was Gandhi Jayanti, today is Saturday), so the age is large but neutral because the market is closed. The pair list reflects default-enabled strategies, so it is the 23 pairs shown, not necessarily Richard's own enabled set. Eligible pairs match the plan's expectation: 4 cpr_trend and 4 ak_roxx_pro coins would qualify; ny_n_break reads "losing overall".

## Safety tripwires

```
B = 9f9a91033f037804de757453975ed0744614beb7   (docs(04): create phase plan)
git diff --name-only $B -- crypto/ executor.py dhan_orders.py exit.py config.py charges.py market_context/
    risk_manager.py trailing.py strategies/ scanner.py tick_feed.py market_log.py      ->  (empty)
ruff check index_ai/   ->  Found 7 errors.   (same 7 as baseline: dhan_auth.py:799, dhan_network.py:49,
                                               dhan_orders.py:569, execution_safety.py:108/118/133/533)
lock tripwire (threading.Lock()/RLock() in index_ai crypto)   ->  13
grep '"/api/data-health"\|"/api/crypto/live-pairs"' index_ai/server.py
    1473:@app.get("/api/data-health", include_in_schema=False)
    1596:@app.get("/api/crypto/live-pairs", include_in_schema=False)       (exactly two, both @app.get)
```

The whole phase changed only: `index_ai/data_health.py` (new), `index_ai/server.py`, `index_ai/strategy_performance.py`, two test files, and six `dashboard/src` files, plus `.planning/` notes.

## Full test suite

`python -m pytest -q` -> **852 passed, 1 failed in 151.56 s**. The one failure is `tests/test_commodities.py::test_lane_plumbing_open_then_close` (IndexError, `scan_commodities_paper()` returned an empty list because `is_mcx_trading_day()` is False on a Saturday) - the known weekend-only failure, unrelated to Phase 4; it passes on trading days. Not touched.

## Whole-phase UIUX-03 checklist (dashboard/src diff from `9f9a910`, applied by hand - no subagent tool)

| # | Check | Finding | Resolution |
|---|-------|---------|------------|
| 1 | Raw colours instead of tokens | None. The plan's grep for hex / `rgb(` and the named Tailwind colour utilities prints 0 on added lines; colours are `var(--up/--warn/--down/--armed)` or `slate` | none needed |
| 2 | Corner-radius drift | None. No `rounded-lg/xl/2xl/3xl` added; the LIVE/PAPER chips use plain `rounded` exactly like the old chips they replace | none needed |
| 3 | Data not marked as data | None. Counts line, chips, trade counts, dollar figures and the "checked ... IST" stamp carry `font-mono tabular-nums`; tile values and sub-lines come from `StatTile` (tabular-nums). Dollar amounts inside the server-written reason sentences are prose by design | none needed |
| 4 | Hand-rolled panels instead of `fx.panel` / `fx.card` | None. `DataHealthPanel` uses `cn(fx.panel, ...)`; `CryptoLivePairs` is a divider block inside the existing arm panel, not a second panel | none needed |
| 5 | Local Date/UTC logic instead of `lib/ist.ts` | None. The plan's grep for `new Date(`, `toISOString(`, `getDay(` prints 0; every time shown is a server-formatted IST string | none needed |
| 6 | Layout math (header / ticker strip / sidebar) | None. Neither `AppShell`, `TickerStrip` nor `Sidebar` was touched; the panel sits in the page body | none needed |
| 7 | Duplicated JSX instead of a shared component | None. `StatTile` reused for all four tiles; no new table/chart | none needed |
| + | Plain words | None. The plan's grep for websocket / stalled / readiness / gate / ladder in the two components prints nothing; wording reads "Market closed", "Running late", "Not updating", "Paused for the close" | none needed |

Side-by-side read of the two components: both use the 11-12.5 px slate-500 sub-text, mono-tabular figures and the same "Market closed"/"Off" vocabulary. Heading styles differ on purpose: the panel heading is `text-sm font-bold` because it is a stand-alone panel title, the crypto list heading is the small uppercase label the crypto panel already uses.

Checks that ran: `npm --prefix dashboard run build` (ok), `npx tsc --noEmit -p tsconfig.app.json` (exit 0), `npx eslint` on `CryptoLivePairs.tsx DataHealthPanel.tsx CryptoExecutionPanel.tsx CryptoPanel.tsx App.tsx shell/StatusPills.tsx` (exit 0). The three acceptance greps print 0 / 0 / nothing.

Trading-safety read of the two new handlers in `server.py` (by hand): both are `@app.get`, `async def` with the work in `asyncio.to_thread`; `/api/data-health` calls only `collect()` (read-only sqlite URI, file tail read); `/api/crypto/live-pairs` calls `crypto_settings()` and the pair view (journal read) and passes `live_orders_enabled` only as a display flag. Neither arms, writes `.env`, or reaches Dhan/Delta. No findings.

## END-OF-PHASE LOOK FOR RICHARD - PENDING (not done by the executor)

Richard restarts the server once (`python -m uvicorn index_ai.server:app --port 8000`) and opens the dashboard with his password. **Do not arm anything** - not crypto, not India - and do not switch any mode.

1. **Index Options page:** a "Data health" panel under the page title with four tiles - Live prices, Option chain, Option spreads, Dhan live feed. On a closed market (evening, weekend, holiday) every tile is grey with "Market closed" (or "Off" if the live price feed is switched off) and the last-seen IST time - nothing amber, nothing red. In market hours fresh data shows green.
2. **Crypto tab:** right under the Paper/Live switch (and under the arm strip in Live mode), a list headed "If you arm: which pairs would use real money", grouped by strategy, coins marked PAPER with a plain reason each. Expect on today's data: cpr_trend and ak_roxx_pro coins that qualify read "ready: new trades use real money once crypto is armed"; ny_n_break reads "this strategy is losing overall so far (...)". The old "Go-live readiness" block lower down is gone.
3. **Phone width:** narrow the window - the tiles drop to two columns, both blocks stay readable, nothing scrolls sideways.
4. **Header "Ticks" pill:** hover - its tooltip points to the Data health panel.
5. **Ask Richard:** is the panel at the top of the Index Options page what he meant by "next to the status pills" (A5)? Is replacing the old Go-live block fine?

Other flagged assumptions to put to him: the spread thresholds 600 s / 1800 s and the 180 s open-bell grace are judgement calls, constants in `index_ai/data_health.py`; the real-data check used default settings, so his own enabled strategy set only shows after the restart.

UIUX-01..03 are therefore **not** marked complete here - the orchestrator marks them after Richard's look. `REQUIREMENTS.md` was not edited.

## Deviations from Plan

None - plan executed as written, with two small notes:

- The plan's `<verify>` pipes `ruff check index_ai/ | tail -1 | grep "Found 7 errors."`, but ruff's last line is "No fixes available (3 hidden fixes ...)"; the "Found 7 errors." line is the one before it. I checked the count directly (7, matching the baseline), so the intent holds.
- The ui-consistency-reviewer and trading-safety-reviewer subagents are not available in this execution context; both checks were applied by hand as above.

## Known Stubs

None.

## Threat Flags

None - no new endpoints, auth paths or schema in this plan; it only read.

## Issues Encountered

None beyond the known weekend-only commodities test failure.

## Self-Check: PASSED

- `.planning/codebase/CONCERNS.md` contains "Resolved 2026-10 (Phase 4)" twice: found.
- Commit `b8b6aca` exists on `main`.
- Money-path diff empty, ruff 7, locks 13, build / tsc / eslint clean, pytest 852 passed + 1 known weekend failure.
