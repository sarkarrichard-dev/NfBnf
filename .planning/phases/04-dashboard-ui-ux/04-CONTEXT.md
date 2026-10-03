# Phase 4: Dashboard UI/UX - Context

**Gathered:** 2026-10-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the dashboard show what is really happening without reading server logs:
(1) which crypto (strategy, coin) pairs are live vs still paper after arming
crypto (UIUX-01); (2) a Data Health view with price-feed (tick) age,
option-chain snapshot age, measured-spread age and Dhan websocket status
(UIUX-02); (3) every new or changed element passes the existing
ui-consistency-reviewer checks (UIUX-03). Display only: no change to trading
logic, arming, strategies, order placement or any stop.

</domain>

<decisions>
## Implementation Decisions

### Live vs paper crypto pairs (UIUX-01)
- **D-01:** Shown **in the Crypto section, right under the arm button**, as a
  clear list: every (strategy, coin) pair marked LIVE or PAPER, and for each
  PAPER pair the plain-language reason it is not live yet (e.g. "only 3
  trades on this coin", "strategy not ready", "losing on this coin"). Richard
  chose this over a global banner. The backend already computes it
  (`strategy_performance.crypto_live_pair_table()` / `crypto_live_pairs()`,
  served around `server.py:1580`) — this is mainly a display job; the list
  must mirror exactly what `crypto/lanes.py` uses to decide live vs paper.
  — **Reversibility:** reversible (display only).

### Data Health (UIUX-02)
- **D-02:** A **small panel on the main page, next to the status pills**
  (always one glance away), not a separate tab. One line each for: tick age
  (and whether the websocket is connected / stalled), option-chain snapshot
  age, measured-spread age, Dhan websocket status. It extends the existing
  "Ticks live / fallback" pill (`StatusPills.tsx`, which says the full view is
  this phase) rather than duplicating it. — **Reversibility:** reversible.
- **D-03:** Staleness colours apply **only while the market is open**
  (amber after about 1 minute, red after about 5 for live prices). When the
  market is closed it says "market closed" in a neutral colour, never red, so
  evenings and weekends do not look broken. Exact thresholds per data type are
  Claude's discretion (chain and spread data refresh more slowly than ticks —
  the scanner records the chain about every 90 s). — **Reversibility:**
  reversible.

### Visual bar (UIUX-03)
- **D-04:** Everything new follows the existing bar: design tokens (no raw
  colours), existing corner radii, mono + tabular-nums on numbers, shared
  primitives, IST helpers from `lib/ist.ts`, plain everyday words for a
  non-technical owner (no internal jargon). The ui-consistency-reviewer is run
  on the result; a dashboard `src` change needs `npm --prefix dashboard run
  build`.

### Claude's Discretion
- Where exactly the data-age values come from (existing endpoints such as
  `GET /api/tick-feed`, `market_log` chain timestamps, `spread_calib`
  measurement dates) and whether one small new read-only endpoint is cleaner
  than several calls; polling interval; exact wording and layout.
- Any new endpoint must be non-blocking in `async def` (use
  `asyncio.to_thread`) and read-only (the `market_log` multi-GB table must
  only be touched with cheap indexed lookups / a read-only connection).

</decisions>

<canonical_refs>
## Canonical References

- `.planning/ROADMAP.md` Phase 4 section; `.planning/REQUIREMENTS.md` UIUX-01..03
- `.planning/codebase/CONCERNS.md` (the "must read server logs" gaps)
- `dashboard/src/components/shell/StatusPills.tsx` — existing tick pill, D-10 note
- `dashboard/src/components/CryptoSetupPanel.tsx`, `CryptoExecutionPanel.tsx`, `CryptoPanel.tsx` — where the arm button and crypto controls live
- `index_ai/strategy_performance.py` (`crypto_live_pairs`, `crypto_live_pair_table`, `crypto_live_readiness`), `crypto/lanes.py` (~401-406, 599: how live is decided)
- `index_ai/tick_feed.py` (`status`, `last_tick_at`), `GET /api/tick-feed` (`server.py` ~1466)
- `index_ai/market_log.py` (chain/ticks tables, `stats()`), `index_ai/market_context/spread_calib.py`
- `.claude/agents/ui-consistency-reviewer.md`; Phase 3's `StrategyPerformancePage.tsx` ExitRecheckPanel as a recent example of the house style
- Memory notes: dashboard UX expectations (instant-feeling controls, "pop" palette), talk in plain language

</canonical_refs>

<specifics>
## Specific Ideas

- Richard's wording of the goal: see "which crypto pairs are really live, whether market data is flowing" without reading logs.
- Plain-language reasons for every PAPER pair; no jargon.

</specifics>

<deferred>
## Deferred Ideas

- Telegram alerts when data goes stale (new capability; not asked for).
- A single global "crypto live" banner on every page (Richard chose the Crypto section; could be added later).

</deferred>

---
*Phase: 04-dashboard-ui-ux*
*Context gathered: 2026-10-03*
