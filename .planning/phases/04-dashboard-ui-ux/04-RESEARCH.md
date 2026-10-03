# Phase 4: Dashboard UI/UX - Research

**Researched:** 2026-10-03 (Saturday, market closed)
**Domain:** FastAPI read-only status endpoints + React/Vite/Tailwind-4 dashboard panels (QuantHawk house style)
**Confidence:** HIGH (everything below was read in the real code or measured read-only against the real data files this session; the few judgement calls are listed in the Assumptions Log)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Shown **in the Crypto section, right under the arm button**, as a clear list: every (strategy, coin) pair marked LIVE or PAPER, and for each PAPER pair the plain-language reason it is not live yet (e.g. "only 3 trades on this coin", "strategy not ready", "losing on this coin"). Richard chose this over a global banner. The backend already computes it (`strategy_performance.crypto_live_pair_table()` / `crypto_live_pairs()`, served around `server.py:1580`) — this is mainly a display job; the list must mirror exactly what `crypto/lanes.py` uses to decide live vs paper. — **Reversibility:** reversible (display only).
- **D-02:** A **small panel on the main page, next to the status pills** (always one glance away), not a separate tab. One line each for: tick age (and whether the websocket is connected / stalled), option-chain snapshot age, measured-spread age, Dhan websocket status. It extends the existing "Ticks live / fallback" pill (`StatusPills.tsx`, which says the full view is this phase) rather than duplicating it. — **Reversibility:** reversible.
- **D-03:** Staleness colours apply **only while the market is open** (amber after about 1 minute, red after about 5 for live prices). When the market is closed it says "market closed" in a neutral colour, never red, so evenings and weekends do not look broken. Exact thresholds per data type are Claude's discretion (chain and spread data refresh more slowly than ticks — the scanner records the chain about every 90 s). — **Reversibility:** reversible.
- **D-04:** Everything new follows the existing bar: design tokens (no raw colours), existing corner radii, mono + tabular-nums on numbers, shared primitives, IST helpers from `lib/ist.ts`, plain everyday words for a non-technical owner (no internal jargon). The ui-consistency-reviewer is run on the result; a dashboard `src` change needs `npm --prefix dashboard run build`.

### Claude's Discretion
- Where exactly the data-age values come from (existing endpoints such as `GET /api/tick-feed`, `market_log` chain timestamps, `spread_calib` measurement dates) and whether one small new read-only endpoint is cleaner than several calls; polling interval; exact wording and layout.
- Any new endpoint must be non-blocking in `async def` (use `asyncio.to_thread`) and read-only (the `market_log` multi-GB table must only be touched with cheap indexed lookups / a read-only connection).

### Deferred Ideas (OUT OF SCOPE)
- Telegram alerts when data goes stale (new capability; not asked for).
- A single global "crypto live" banner on every page (Richard chose the Crypto section; could be added later).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| UIUX-01 | The dashboard shows which (strategy, coin) pairs are actually live vs. paper after arming crypto | Live/paper rule is `live and (strat, sym) in live_pairs` (lanes.py:599); a new read-only `GET /api/crypto/live-pairs` that applies that same rule over the pairs the lane actually runs; new list component mounted inside `CryptoExecutionPanel` under the arm strip (sections 1, Pattern 1) |
| UIUX-02 | A "Data Health" view shows tick age, option-chain snapshot age, measured spread age, and Dhan websocket status | New read-only `GET /api/data-health` (ages + market flag + server-side status words); in-memory tick feed, indexed read-only chain lookup, tail-read of `spread_samples.jsonl`; panel at the top of the main page (Pattern 2, Pitfalls 1-6) |
| UIUX-03 | Every dashboard change meets the existing visual bar (tokens, radius, mono/tabular-nums) | Reviewer's 7 checks turned into grep-able acceptance criteria (section "UIUX-03 acceptance checklist"); primitives to reuse listed in Standard Stack |
</phase_requirements>

## Summary

Phase 4 is display-only and needs **no new packages** and **no change to any trading path**. Two small read-only backend endpoints plus two small dashboard components cover everything.

**UIUX-01.** The server already decides live vs paper in exactly one place: `live=live and (strat, sym) in live_pairs` at `crypto/lanes.py:599`, where `live = s.live_orders_enabled` (lanes.py:398) and `live_pairs = crypto_live_pairs()` (lanes.py:406). The existing endpoint `GET /api/crypto/live-readiness` already returns a per-pair table, and `CryptoPanel.tsx` already consumes it (a "Go-live readiness" block at CryptoPanel.tsx:614-685, mounted far below the arm button at :461). But that table is **not safe to mirror as-is**: measured against the real journal today it lists pairs the lane never trades (retired gold/TRX/ADA coins, the paper-only `btc_daily_straddle`, coins that `_STRATEGY_SYMBOLS` removed from a strategy), omits active pairs that have no paper trades yet, prints "only 1 trades", and has no notion of "armed". The fix is one new pure function that (a) builds the set of pairs the lane really runs from the lane's own helpers, (b) applies the same `armed and pair in crypto_live_pairs()` rule, and (c) returns plain-language reasons; the UI then only renders it. The old LiveReadinessRow duplicates this with jargon and `rounded-lg` tiles and should be replaced by the new list.

**UIUX-02.** One new endpoint `GET /api/data-health` is cleaner than stitching three calls: it returns ages in seconds, a market-open flag and a server-side verdict word per line (`ok`/`slow`/`stale`/`closed`/`off`/`none`), so the colour rules (D-03) are unit-testable in pytest (there is no JS test runner). Sources: tick age and websocket state from the in-memory `tick_feed.status()`; chain age from a **read-only** SQLite connection using the covering index `idx_chain_session(session, instrument, ts)` (measured: ~0.0001-0.003 s on the 7 GB DB); spread age from the **tail** of `memory/spread_samples.jsonl`. Three traps were measured: `SELECT MAX(ts) FROM ticks` is a full scan (**32 s**), `market_log.connect()` runs migrations/commits (not read-only), and `spread_calib.summary()/status()` re-reads the whole 17 MB file.

**Primary recommendation:** add `GET /api/crypto/live-pairs` and `GET /api/data-health` (both `async def` + `asyncio.to_thread`, GET only, no params), keep all verdict logic in Python with pytest coverage, build `CryptoLivePairs` (mounted inside `CryptoExecutionPanel` under the arm strip) and `DataHealthPanel` (top of the Index Options page, under the page title) from `fx.panel` / `fx.card` / `StatTile` / `Button` only, and finish with `npm --prefix dashboard run build` + `tsc` + `eslint` + the manual look.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Decide which crypto pairs are live vs paper | API / Backend (pure function over the journal + lane config) | Browser (render only) | The rule is money-path logic in `lanes.py`; the UI must not re-derive it (single source of truth) |
| Plain-language "why paper" text | API / Backend | Browser (grouping/wording of strategy names via `STRATEGIES[].name`) | The reasons come from the same numbers (`CRYPTO_PAIR_MIN_TRADES`, readiness) |
| Tick age / websocket status | API / Backend (in-memory `tick_feed._state`) | Browser | Only the server process holds the socket |
| Chain snapshot age | Database / Storage (market_log.sqlite, indexed lookup) | API / Backend | Read-only lookup, never COUNT/scan |
| Measured spread age | Database / Storage (jsonl file tail) | API / Backend | File append log written by the scanner stage |
| Market-open / square-off flag, staleness colour verdict | API / Backend (`market_clock`) | Browser maps verdict -> token colour | Server clock + holiday calendar is the truth; browser clock/timezone must not decide |
| Poll cadence, layout, tokens | Browser | — | react-query `refetchInterval` + `usePollMs` |

## Standard Stack

### Core (all already in the repo — nothing to install)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI (existing `index_ai/server.py` app) | per pyproject `fastapi>=0.115` [VERIFIED: pyproject.toml] | New read-only GET endpoints | Every status endpoint here is `@app.get(..., include_in_schema=False)` + `asyncio.to_thread` (server.py:1465-1470, 1576-1585, 1598-1604) |
| sqlite3 (stdlib) | Python 3.14.2 [VERIFIED: `python --version`] | Read-only chain lookup via `file:...?mode=ro` URI | Verified recipe below; avoids `market_log.connect()` write side-effects |
| @tanstack/react-query | ^5.100.14 [VERIFIED: dashboard/package.json] | Poll the two endpoints | Same pattern as `StatusPills`, `useExitRecheck` |
| React 19 + Tailwind 4 + clsx/tailwind-merge (`cn`) | ^19.2.6 / ^4.3.0 [VERIFIED: dashboard/package.json] | Components | House stack |
| pytest | 9.0.3 [VERIFIED: `python -m pytest --version`] | Backend tests | Existing suite |

### Existing dashboard primitives to reuse (UIUX-03)
| Primitive | Where | Use for |
|-----------|-------|---------|
| `fx.panel`, `fx.card`, `fx.cardLabel`, `fx.cardValue` | `dashboard/src/lib/theme.ts:6-12` | Panel container, tile fill, label, mono tabular value. Verbatim: `panel: 'rounded-md border border-[var(--hair)] bg-[var(--panel)]'`, `card: 'rounded-md border border-[var(--hair-soft)] bg-white/[0.02] px-3 py-2.5'`, `cardValue: 'mt-1 font-mono text-sm font-semibold tabular-nums leading-tight text-slate-50'` |
| `StatTile` (`label`, `value`, `sub`, `valueClass`) | `dashboard/src/components/ui/StatTile.tsx` | The four Data Health cells — it already carries mono + tabular-nums via `fx.cardValue`, and `valueClass` accepts a token class |
| `Button` | `dashboard/src/components/ui/Button.tsx` | Only if a button is needed (none is required this phase) |
| `Pill` (private in StatusPills.tsx:50-76) | not exported; has `hidden ... sm:inline-flex` (StatusPills.tsx:63) | **Do not reuse for the panel** (it disappears below `sm`); reuse only its tone mapping idea |
| `STRATEGIES[].name` | `dashboard/src/lib/strategies.ts:29-33` (`id`, `name`) | Show "NY N-Break" style names instead of `ny_n_break` ids |
| `usd` / `usd0` / `pnlCls` | `dashboard/src/lib/cryptoFmt.ts:14,26,33` | Net P&L in the pair list (`pnlCls` returns `text-[var(--up)]`/`text-[var(--down)]`/`text-slate-400`) |
| `usePollMs`, `api`, `useQuery` | `hooks/usePageVisible.ts:23`, `lib/api.ts`, react-query | Polling that deliberately ignores tab visibility (Richard 2026-09-16, usePageVisible.ts:18-22) |
| `lib/ist.ts` | `istDayBoundsMs`, `istTodayDate`, ... | **Not needed**: the server sends ages in seconds and an IST display string, so the front end does no date math at all |
| Tokens | `dashboard/src/index.css:65-83` | `--acc`, `--up`, `--down`, `--warn`, `--armed`, `--ground`, `--panel`, `--hair`, `--hair-soft` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| New `/api/data-health` | Reuse `/api/tick-feed` + `/api/market-log` + `/api/spread-calibration` | `/api/market-log` runs `stats()` with `COUNT(*)` over the multi-GB `ticks` table (server.py:1484 comment: "seconds, off-loop"); `/api/spread-calibration` is a sync `def` that re-reads the 17 MB file 6 times (spread_calib.status). Wrong for a 20 s poll. |
| Server-side verdict words | Colour logic in TSX | No JS test runner exists, so TSX logic is unverifiable by automation; Python logic is pytest-able |
| Extend `/api/crypto/live-readiness` | New `/api/crypto/live-pairs` | The old response is consumed by `LiveReadinessRow`; a new endpoint leaves it untouched (it can be left as an unused API) |

**Installation:** none. **Version verification:** versions above were read from `dashboard/package.json`, `pyproject.toml` and the local toolchain (node v24.16.0, npm 11.13.0, Python 3.14.2, pytest 9.0.3) this session.

## Package Legitimacy Audit

No external packages are installed in this phase. Not applicable.

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
                              (every /api route sits behind the DASHBOARD_PASSWORD gate)

 Browser (react-query, usePollMs)                         FastAPI process (single process, single event loop)
 ─────────────────────────────────                        ──────────────────────────────────────────────────
 CryptoExecutionPanel  ──GET /api/crypto/live-pairs──►   async handler ─► asyncio.to_thread(_read)
   └─ CryptoLivePairs (under arm strip)  (60 s poll)         _read():
        renders LIVE/PAPER + reason                            crypto_settings().live_orders_enabled   ── armed?
                                                               lanes._enabled_strategies(s) × lanes._symbols_for(strat, s)
                                                                    └─► ACTIVE pair set (what the lane loop really visits, lanes.py:427-428)
                                                               strategy_performance.crypto_live_pairs() ─► journal (memory/crypto_journal.jsonl, 271 KB)
                                                               pair is LIVE  iff  armed AND pair ∈ crypto_live_pairs()   (== lanes.py:599)
                                                               reason text for every PAPER pair (plain words)

 Index Options page (App.tsx tab 'trade')                async handler ─► asyncio.to_thread(_read)
   └─ DataHealthPanel (under PageHeader) ─GET /api/data-health─►   _read():
        4 StatTiles: prices · chain · spreads · Dhan feed (20 s poll)  market_clock.is_market_open() / is_square_off_window()
        header Ticks pill (optional) shares the same query key        tick_feed.status()            (in memory, no I/O)
                                                                      chain:  sqlite file:...?mode=ro  → MAX(session) then MAX(ts) per index
                                                                      spread: tail 64 KB of memory/spread_samples.jsonl → last "at" per index
                                                                      classify(age, market_open, amber, red) → ok/slow/stale/closed/off/none
```

### Recommended Project Structure
```
index_ai/
├── data_health.py                 # NEW: read-only collectors + pure classify(); no I/O at import
├── strategy_performance.py        # add crypto_live_pair_view(active_pairs, armed) (pure; next to crypto_live_pair_table)
└── server.py                      # add 2 GET endpoints beside /api/crypto/live-readiness (~line 1576)
tests/
├── test_data_health.py            # NEW
└── test_crypto_live_pairs.py      # extend (view function parity cases)
dashboard/src/
├── hooks/useDataHealth.ts         # NEW (types + query), pattern of useExitRecheck.ts
├── hooks/useCryptoLivePairs.ts    # NEW
├── components/DataHealthPanel.tsx # NEW
├── components/CryptoLivePairs.tsx # NEW; mounted in CryptoExecutionPanel.tsx
├── components/CryptoPanel.tsx     # remove LiveReadinessRow + its `readiness` query (superseded)
└── App.tsx                        # mount <DataHealthPanel /> in the 'trade' tab
```

### Pattern 1: UIUX-01 — one rule, mirrored exactly

**What the lane does (verbatim, crypto/lanes.py):**
```python
398:    live = s.live_orders_enabled
399:    # armed != every pair live: only (strategy, coin) pairs that cleared the
400:    # readiness bar get real orders; the rest keep paper-trading alongside.
401:    live_pairs: set[tuple[str, str]] = set()
402:    if live:
403:        from index_ai.strategy_performance import crypto_live_pairs
...
406:            live_pairs = crypto_live_pairs()
407:        except Exception:
408:            logger.exception("crypto live-pair read failed -- every entry stays paper this scan")
...
427:    for strat in strategies:
428:        for sym in _symbols_for(strat, s):
...
599:                        live=live and (strat, sym) in live_pairs,
```
[VERIFIED: crypto/lanes.py:398-409, 427-428, 599] `strategies = _enabled_strategies(s)` (lanes.py:421). `live_orders_enabled` is `self.trading_mode.upper() == "LIVE" and self.live_armed and self.credentials_ready` [VERIFIED: crypto/config.py:180-182]. `crypto_live_pairs` requires the strategy to be ready (`CRYPTO_LIVE_MIN_TRADES = 30`, `CRYPTO_LIVE_MIN_DAYS = 14`, net > 0, strategy_performance.py:326-327, 354-358) and the coin to have `row["trades"] >= CRYPTO_PAIR_MIN_TRADES` (`CRYPTO_PAIR_MIN_TRADES = 5`, :384) with `row["net"] > 0` (:387-403).

**Recommended view function** (additive, pure, no lanes.py edit):
```python
# strategy_performance.py — next to crypto_live_pair_table (read-only)
def crypto_live_pair_view(active: set[tuple[str, str]], armed: bool) -> list[dict]:
    """Every pair the lane really visits, marked live/paper with a plain reason.
    live  ==  armed and pair in crypto_live_pairs()   (crypto/lanes.py:599)."""
    allowed = crypto_live_pairs()
    ready = {r["strategy"]: r for r in crypto_live_readiness()}
    table = {(r["strategy"], r["coin"]): r for r in crypto_live_pair_table()}
    ...
```
Response shape (recommended): `{"armed": bool, "read_ok": bool, "pairs": [{"strategy","coin","trades","net_usd","eligible": bool, "live": bool, "reason": str|None}]}` where `eligible` = pair in `crypto_live_pairs()` and `live = armed and eligible`. The endpoint computes `active` from `lanes._enabled_strategies(s)` and `lanes._symbols_for(strat, s)` (the literal two calls in the lane's loop) and `armed` from `crypto_settings().live_orders_enabled`, so the payload is self-consistent and the front end never combines two polls.

**Mirror rules the view must obey (these are the disagreement points found):**

| # | Where UI and lane could disagree | Evidence | Rule for the view |
|---|----------------------------------|----------|-------------------|
| 1 | Pairs in the journal that the lane no longer visits (gold PAXG/XAUT, TRX, ADA — removed from the allowlist; coins outside `_STRATEGY_SYMBOLS`, e.g. `ny_n_break` limited to BTC/ETH, `rsi_adx_trend` to BTC/ETH/SOL — lanes.py:146-160) | Real table today lists 41 rows incl. `ak_roxx_pro PAXGUSD "losing on this coin"`, `ny_n_break XRPUSD`, `cpr_trend XAUTUSD`; the real `s.symbols` is `('BTCUSD','ETHUSD','SOLUSD','XRPUSD','BNBUSD','DOGEUSD')` | Show **only** `active = {(st, sym) for st in _enabled_strategies(s) for sym in _symbols_for(st, s)}` |
| 2 | `btc_daily_straddle` is in the journal but has no live path at all (btc_straddle.py:18-22 "Paper only, no live path at all") and is not in `_enabled_strategies` (lanes.py:122-140) | Real table shows `btc_daily_straddle BTCUSD ... -7363.21`; if it ever cleared the bar `crypto_live_pairs()` would include it | Excluded by rule 1 (not in `active`). Never display it as LIVE |
| 3 | Active pairs with **zero** paper trades never appear in `crypto_live_pair_table()` (it iterates journal rows) | e.g. `ichimoku XRPUSD`, `rsi_adx_trend` coins with no rows are absent today | Left-join: add `trades: 0`, reason "no trades on this coin yet" |
| 4 | Strategy with no readiness record -> existing text would print `strategy not ready: None` (`strat.get('why_not')` on `{}` , strategy_performance.py:415-420) | Only reachable once rule 3 adds zero-trade pairs | Reason "no paper trades yet (needs 30)" |
| 5 | Not armed: lane forces **everything** paper (`live` False) but `crypto_live_pairs()` still returns what *would* go live | lanes.py:398,402,599 | `live` False for all; keep `eligible` so UI can say "would send real orders once armed" |
| 6 | `armed` must be `live_orders_enabled`, not `live_armed`: `CryptoExecutionPanel` uses `armed = !!s?.live_armed` (CryptoExecutionPanel.tsx:68), but missing Delta keys or Paper mode make the lane paper | crypto/config.py:180-182 | Server computes `armed = live_orders_enabled` |
| 7 | Read failure: lane treats an unreadable pair list as "everything paper" (lanes.py:407-408) | | Endpoint returns `read_ok: false`, every pair `live: false`; UI says it could not be read, not an error box |
| 8 | An already-open position keeps the mode it was opened in (`pos.get("mode") == "live"` drives exit, lanes.py:607); only **new entries** follow the list | lanes.py:599,607 | Wording: "new trades will use real money" / "paper only"; do not claim open positions flip |
| 9 | A LIVE pair can still be skipped at order time (sizing, ML gate, `executor.live_gate` kill switch, max-open caps, entry window) — these produce a "wait", never a downgrade to paper (lanes.py:963-989) | | Wording for LIVE: "sends real orders" not "is trading now"; the kill-switch line already exists in the panel (CryptoExecutionPanel.tsx:287-292) |
| 10 | Window/session: new entries only inside the crypto session (lanes.py:395, 572-581) | | Optional one-line note; not a live/paper difference |

**Parity test (cheap, pytest):** assert `active == {(st, sym) ...}` built the same way the lane loops; assert armed=False -> no pair `live`; assert a ready strategy + profitable coin is `live` only when armed; assert the straddle and a removed coin never appear; assert `crypto_live_pairs` raising -> `read_ok False`, nothing live. Reuse the monkeypatch idiom of tests/test_crypto_live_pairs.py (`_journal(...)`, `monkeypatch.setattr(sp, "data_epoch", lambda: None)`) and tests/test_crypto_phase4.py:212-214 (`monkeypatch.setattr("index_ai.strategy_performance.crypto_live_pairs", lambda: {...})` — the lane imports it lazily at call time, so a patched module attribute is honoured; the new view must also call `crypto_live_pairs` through the module so the same patch works).

**Mount point (UIUX-01).** `CryptoExecutionPanel` (rendered at the very top of the Crypto tab, CryptoPanel.tsx:253) holds the Paper/Live switch and the arm strip: the strip with **"Arm live orders"** / **"Disarm"** is at CryptoExecutionPanel.tsx:236-265 and only renders `{isLive ? ... : null}`. Mount `<CryptoLivePairs />` **immediately after that block (after line 265)** and before the `isLive` kill-switch `<dl>` (:267-294) — inside the same `fx.panel` section so it stays "right under the arm button". In Paper mode there is no arm button, so the list still renders directly under the switch row with the not-armed wording. Heading states: not armed -> "If you arm: which pairs would use real money"; armed -> "Right now: which pairs use real money". 30 s poll is plenty (the lane reads the journal per scan; readiness moves per closed trade); CryptoPanel's own poll for the old block was 5 min (CryptoPanel.tsx:182) — use 60 s.

**Supersede, don't duplicate:** `LiveReadinessRow` (CryptoPanel.tsx:614-685) already shows pair chips with reasons only in a hover `title` (:665), uses the words "Go-live readiness", "READY", "not yet", and uses `rounded-lg` on tiles (:636) — a reviewer check-2 hit. Delete it and its `readiness` query (CryptoPanel.tsx:178-183, 461-466, type blocks 125-145) once the new list exists. The old API endpoint can stay unconsumed.

**Plain wording (D-01/D-04):** use `STRATEGIES.find(s => s.id === strategy)?.name ?? strategy` for names, coin without the `USD` suffix (the old code did `p.coin.replace(/USD.?$/, '')`, CryptoPanel.tsx:675). Backend reason strings to emit (keep the substrings the existing test asserts: `losing on this coin`, `only 3 trades`, `strategy not ready`):
- ready, profitable, enough trades -> LIVE, no reason (or "used real money once armed" when not armed)
- coin too few trades -> "only 3 trades on this coin so far (needs 5)" (fix the existing "only 1 trades" plural, strategy_performance.py:421)
- coin losing -> "losing on this coin so far (−$12.30)" (existing format prints `$-12.30`, :423)
- strategy not ready -> "this strategy needs 30 trades first (12 so far)" / "needs 14 days of results (6 so far)" / "losing overall so far (−$143.70)"
- no data -> "no trades on this coin yet"

Group by strategy in the UI and print a strategy-level reason once when every PAPER coin of that strategy shares it (today 5 strategies × up to 6 coins = ~23 rows; flat 23 rows is too long for a glance).

### Pattern 2: UIUX-02 — one read-only endpoint, verdict computed server-side

**Where each age comes from today, and its cost/safety**

| Line | Source | Cost / safety | Evidence |
|------|--------|---------------|----------|
| Price (tick) age + websocket connected/stalled | `tick_feed.status()` — in-memory `FeedState.as_dict()` | Zero I/O. Keys (verbatim): `"enabled"`, `"connected"`, `"ticks"`, `"reconnects"`, `"seconds_since_last_tick"`, `"stalled"`, `"last_error"`. `stalled` = age > `STALL_SECONDS = 90.0` | tick_feed.py:48, 141-155 [VERIFIED] |
| Option-chain snapshot age | `chain` table `ts` (IST ISO with `+05:30`), covering index `idx_chain_session ON chain(session, instrument, ts)` | **Measured read-only on the real 7.0 GB DB**: `MAX(session)` 0.003 s, `MAX(ts) WHERE session=? AND instrument=?` 0.0001-0.0003 s each; query plans `SEARCH chain USING COVERING INDEX idx_chain_session` | market_log.py:132-164; plans measured 2026-10-03 [VERIFIED] |
| Measured-spread age | last `at` in `memory/spread_samples.jsonl` (IST ISO) | File is 16.9 MB / 70,584 lines — tail-read only (last ~64 KB ≈ several scan cycles) | spread_calib.py:27,75; file stat 2026-10-03 [VERIFIED] |
| Dhan REST/auth health | `dhan_ready` already in `/api/status` (App.tsx:215 passes it to `StatusPills`) and `check_dhan_health` (cached `_HEALTH_CACHE_SEC = 45.0`, makes network calls) | **Do not call `check_dhan_health` from the new endpoint** (network I/O); the existing "Dhan OK" pill covers it | dhan_auth.py:1022,1062; server.py:774 |

**Dhan websocket status** = the tick feed's `enabled` / `connected` / `reconnects` / `last_error` (the websocket exists only when `ENABLE_TICK_FEED` is on; it is off by default, tick_feed.py:24, 53-54, and toggling needs a restart, server.py:1434). Off is a neutral, plain-words state ("Live price feed is switched off — stops are checked every 20 seconds"), which is what the current pill title already says (StatusPills.tsx:22-26).

**Read-only chain lookup — verified recipe (Windows path):**
```python
# Source: executed against the real memory/market_log.sqlite 2026-10-03
import sqlite3
from index_ai import market_log            # read market_log.DB_PATH at CALL time (tests monkeypatch it, conftest.py:60)
uri = market_log.DB_PATH.resolve().as_uri() + "?mode=ro"     # file:///C:/Richard%20Docx/.../market_log.sqlite?mode=ro
db = sqlite3.connect(uri, uri=True, timeout=2)
sess = db.execute("SELECT MAX(session) FROM chain").fetchone()[0]
ts = db.execute("SELECT MAX(ts) FROM chain WHERE session=? AND instrument=?", (sess, "NIFTY")).fetchone()[0]
```
A missing file raises `OperationalError: unable to open database file` and does **not** create the file (verified), so catch it and return `age_seconds: None`. Use `?mode=ro`, never `market_log.connect()` (it runs `_migrate` — 4 `CREATE TABLE IF NOT EXISTS` + 7 `CREATE INDEX IF NOT EXISTS` + `PRAGMA journal_mode=WAL` + `commit`, market_log.py:57-70, 73-166 — write-capable, and it would also create an empty DB in a test that forgot to redirect). Parse timestamps with `market_clock.parse_ist_datetime` (handles `+05:30`) and subtract `market_clock.now_ist()`; both are tz-aware so no UTC/IST slip.

**Tail-read of the spread file (never `spread_calib.summary()/status()`):** `_samples()` does `SAMPLES_PATH.read_text(...).splitlines()` over the whole file (spread_calib.py:128-144) and `status()` calls it per instrument several times (:216-224). Instead open `rb`, `seek(max(0, size-65536))`, drop the first partial line, `json.loads` each remaining line, keep the last `at` per `instrument`. Read `spread_calib.SAMPLES_PATH` at call time (conftest.py:63-64 patches it). Do **not** read `spread_skips.json`: its `last_at` is polluted (`"NIFTY": {"no_strike": 310, "last_at": "2026-10-03T17:20:42+05:30"}` on a Saturday — `conftest.py` patches `SAMPLES_PATH` but not `SKIPS_PATH`, so a test run wrote the real file; it records skipped attempts, not measurements).

**Recommended response** (`GET /api/data-health`, no parameters):
```json
{
  "as_of": "2026-10-03T10:15:42+05:30",
  "market_open": true,
  "square_off_window": false,
  "ticks":  {"status": "ok", "enabled": true, "connected": true, "stalled": false,
             "age_seconds": 2.1, "reconnects": 0, "last_error": null},
  "chain":  {"status": "ok", "age_seconds": 41, "last_at": "2026-10-03T10:15:01+05:30",
             "by_index": {"NIFTY": 41, "BANKNIFTY": 47, "SENSEX": 52}},
  "spread": {"status": "ok", "age_seconds": 63, "last_at": "...", "by_index": {"NIFTY": 63, ...}}
}
```
Per line `age_seconds` = the **oldest** of the configured, un-paused indices (`instruments.configured_index_keys()` already drops paused ones, instruments.py:150-155), with `by_index` for a tooltip. `status` ∈ `ok | slow | stale | closed | off | none`.

**Pure classifier (put in `index_ai/data_health.py`, unit-test it):**
```python
def classify(age, market_open, amber, red, *, in_grace=False, paused=False):
    if not market_open or paused: return "closed"     # neutral — D-03
    if age is None:               return "none"       # "no readings yet" (neutral-ish)
    if age >= red:   return "slow" if in_grace else "stale"
    if age >= amber: return "slow"
    return "ok"
```
UI maps `ok -> var(--up)`, `slow -> var(--warn)`, `stale -> var(--down)`, `closed|off|none -> text-slate-400`. **Use `--down`, not `--armed`, for red**: index.css reserves `--armed` for "an 'armed' red that only shows when live orders are enabled" (index.css:67-68 comment).

**Thresholds (D-03 fixes only the live-price pair; the rest is discretion):**

| Line | Amber | Red | Basis |
|------|-------|-----|-------|
| Live prices | 60 s | 300 s | Locked by D-03 |
| Option chain | 300 s | 600 s | Measured cadence 10:00-14:00 on 2026-09-25 and 09-30: median gap 58 s / 56 s, p95 155 s / 153 s, **max 227 s / 181 s** (225-231 snapshots per index) — 3 min amber would flash in a healthy session; throttle floor is 55 s (`CHAIN_MIN_GAP_SECONDS = 55`, market_log.py:231) and the scan interval is 90 s (`SCAN_INTERVAL_SECONDS = 90`, scanner.py:48); CONCERNS.md suggests alerting at 5 min |
| Measured spread | 600 s | 1800 s | [ASSUMED] The stage runs every cycle while the market is open (scanner.py:994; daily_ops.py:76 gate `is_market_open()`), but the calibration uses a rolling 4000-sample median (spread_calib.py:30), so only a long silence matters |

**Grace and the square-off hole (both measured):**
- The chain is recorded by the planner inside the `index_scans` stage, which the scanner skips once `is_square_off_window()` is true (scanner.py:1007-1028). On 2026-09-25 NIFTY chain stopped at 15:09 (rows only at 15:05, 15:07, 15:09 in the 15:05-15:40 slice). `market_clock.is_market_open()` stays true until 15:30 (market_clock.py:171), so from 15:15 to 15:30 chain age would go red for no fault. Rule: **chain** line is `closed` (neutral, e.g. "paused for the close") when `square_off_window` is true. Spread and ticks keep running to 15:30.
- Open-bell grace: at 09:15 yesterday's/overnight rows are old (the chain is also written at night, e.g. last rows 2026-10-01 20:55; 2026-10-03 18:02 on a Saturday), so within the first ~3 minutes of the session cap at `slow` (`in_grace=True`). `session_times()["market_open"]` gives the open time (market_clock.py:37-40).

**Real outages this view would have exposed (evidence it is worth building):** spread samples stopped mid-session on 2026-09-21 (13:49), 09-23 (13:31), 09-29 (11:26), 10-01 (**09:32**); no chain snapshots at all between 10:00 and 14:00 on 2026-10-01; the last `observations` row is `2026-10-01T09:32:36+05:30`. Today the spread line would read ~2.4 days old — with the market closed it must still show neutral "market closed", with the last-measured IST time as a sub-line (use server-formatted `last_at`, not browser math).

**Mount point (UIUX-02).** The status pills live in `AppShell`'s sticky header, `topRight` (App.tsx:211-217; AppShell.tsx:26-39), header height `h-[2.85rem]` (AppShell.tsx:27) and `TickerStrip` `h-10` (TickerStrip.tsx:60,70) — Sidebar offsets are tied to those, and the reviewer's check 6 forbids changing one without the others. A panel cannot go **in** the header without touching that math, and the pills are hidden below `sm` (StatusPills.tsx:63: `'hidden items-center rounded-full border px-2.5 py-1 text-[11px] font-medium sm:inline-flex'`), so at phone width there is no pill row to sit next to. Recommended (no layout math touched, works at phone width): render `<DataHealthPanel />` at the **top of the Index Options (main) page, directly under `PageHeader` and above `StatsOverview`** (App.tsx:230-249). Layout: `fx.panel` with a compact heading + `grid grid-cols-2 gap-2 sm:grid-cols-4` of four `StatTile`s (Live prices / Option chain / Option spreads / Dhan live feed). The header "Ticks live / fallback" pill stays the one-glance summary on every tab; update its title to point to the panel and (optional) feed it from the same `['data-health']` query to avoid two overlapping polls. If the plan edits StatusPills.tsx, do not add `new Date(` (the existing `istClock()` at StatusPills.tsx:41-48 predates the reviewer and will not appear in the diff unless touched).

Poll: 20 s via `usePollMs(20_000)` like the pill (StatusPills.tsx:95-100: `refetchInterval: 20_000, staleTime: 10_000`). All reads are in-memory or sub-millisecond indexed, plus a 64 KB file tail; negligible next to `/api/status` (~250 ms, server.py:744-746 comment).

**Endpoint skeleton (pattern from server.py:1598-1604):**
```python
@app.get("/api/data-health", include_in_schema=False)
async def data_health_api() -> dict[str, Any]:
    """Ages of the data the bot trades on. Read-only; nothing here changes a setting."""
    from index_ai.data_health import collect
    return await asyncio.to_thread(collect)
```
`collect()` must never raise (wrap each source; a failed source returns `status: "none"` with `age_seconds: None`), because one broken file must not blank the whole panel.

### Anti-Patterns to Avoid
- **Querying `ticks` for tick age** — `SELECT MAX(ts) FROM ticks` is a full table scan (plan `SEARCH ticks`, **31.9 s measured** — no index on `ts` alone, only `(session, instrument)` and `(security_id, ltt)`, market_log.py:162-163). Use `tick_feed.status()`.
- **`COUNT(*)` on any market_log table** (what `market_log.stats()` does, market_log.py:386-409).
- **Colour logic in TSX** (untestable here) and **date math in the browser** (reviewer check 5; the server sends seconds).
- **A `rounded-lg`/`rounded-xl` panel or a hand-written `rounded-md border border-[var(--hair)] bg-[var(--panel)]`** (reviewer checks 2 and 4).
- **Showing red on a closed market** (D-03).
- **Adding a POST/PUT, a parameter, or any call into executor/dhan_orders/exit/risk code** (see Trading-safety).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Is the market open / holiday / square-off window | New hour math in Python or JS | `market_clock.is_market_open()` / `is_square_off_window()` / `session_times()` (market_clock.py:171, 201-208, 37) which consult `market_holidays` via `is_trading_day` (:126-132) | Weekends and NSE holidays already handled; browser clock/timezone are not trustworthy |
| Parse a stored IST timestamp | `datetime.strptime` guesses | `market_clock.parse_ist_datetime` (market_clock.py:76-113) | Handles ISO with offset, Dhan format, naive-as-IST |
| Live/paper decision | A second copy of the readiness rule | `strategy_performance.crypto_live_pairs()` + `crypto_live_readiness()` + the lane's `_enabled_strategies`/`_symbols_for` | Single source of truth; the lane itself calls these |
| Tick staleness | Timestamps from the DB | `tick_feed.status()` | In-memory, already includes the stall rule |
| Stat cards / panels / buttons | New markup | `StatTile`, `fx.panel`, `fx.card`, `Button` | Reviewer checks 4 and 7 |
| Strategy display names | A new id->name map | `STRATEGIES` in `dashboard/src/lib/strategies.ts` | Already has `name` |

**Key insight:** the risk in this phase is not building UI, it is showing a **wrong** "LIVE" or a **false** red. Both are removed by computing the verdict on the server from the same functions the trading code uses.

## Common Pitfalls

### Pitfall 1: A "LIVE" label for a pair the lane never trades
**What goes wrong:** UI says LIVE for `cpr_trend`/`XAUTUSD`-style historical pairs or the paper-only straddle. **Why:** `crypto_live_pair_table()` iterates journal rows, not the lane's pair list. **Avoid:** build from `active` (table in Pattern 1). **Warning sign:** a pair in the list that is not in `_enabled_strategies × _symbols_for`.

### Pitfall 2: Armed flag mismatch
`live_armed` is true but credentials missing or mode Paper -> lane stays paper. Use `live_orders_enabled` computed server-side (crypto/config.py:180-182). Warning sign: green "armed" strip and LIVE chips with Delta keys empty.

### Pitfall 3: `SELECT MAX(ts) FROM ticks` (32 s) / `COUNT(*)` / `market_log.connect()` in a handler
Stalls the thread pool, holds locks and (connect) writes. Use the read-only URI + the `chain` covering index + `tick_feed.status()`.

### Pitfall 4: False red at 15:15-15:30 and at 09:15
Chain stops when square-off starts (15:10) though `is_market_open()` is true until 15:30; overnight rows are old at the bell. Use `square_off_window` and the 3-minute grace (Pattern 2).

### Pitfall 5: Chain age is not proof the scanner is healthy
The chain is also written at night and on weekends (last rows 2026-10-03 18:02, a Saturday) by non-scanner callers (`plan_instrument` is reachable from dashboard/preview paths, planner.py:63-76, 155-157). Under D-03 it only matters while the market is open, where a fresh chain row is the right signal; label it "Option chain", not "Scanner".

### Pitfall 6: Tick age is 0 right after a (re)connect without a tick
`_state.last_tick_at = time.monotonic()` is set on connect (tick_feed.py:242), so age restarts from the connection, not the last real tick; a connected-but-silent socket reads healthy for up to 90 s until `stalled`. Acceptable (that is the feed's own stall rule); show `connected`/`reconnects` next to the age.

### Pitfall 7: Timezones
Chain `ts`, spread `at`, `session` are IST ISO strings; the crypto lane uses UTC in places (`now_utc`, lanes.py:390). The data-health endpoint only handles IST-stamped values and server-side `now_ist()`; the front end gets seconds + an IST display string and does no `new Date()` work.

### Pitfall 8: Stale bundle / needs-restart
`dashboard/dist` is what the server serves; run `npm --prefix dashboard run build` after any `dashboard/src` change (CLAUDE.md), and Richard must **restart the server once** to get the two new endpoints — until then the panels must show a calm sentence ("not available yet — the app may need a restart") exactly like the Phase 3 panel (03-06-SUMMARY: `isError` with no cached data).

### Pitfall 9: Polling cost / duplicate polls
`/api/crypto/live-readiness` read the whole crypto journal several times per call (`_crypto_rows` is called by `crypto_live_readiness` twice and by `crypto_live_pairs`/`table`); the journal is 271 KB so a 60 s poll is fine, but compute the view with one `crypto_live_pairs()` call per request, not per pair. Keep the data-health poll at 20 s and share one query key between pill and panel.

### Pitfall 10: Tests leak into real files
`tests/conftest.py` redirects `market_log.DB_PATH` and `spread_calib.SAMPLES_PATH` (conftest.py:60-64) but **not** `spread_calib.SKIPS_PATH`; any Phase-4 test that calls `spread_calib.observe(...)` must also `monkeypatch.setattr(spread_calib, "SKIPS_PATH", tmp_path / "skips.json")`. Read module attributes at call time in the new code so the redirects apply. Telegram vars are already cleared by the autouse fixture (conftest.py:41-42) — never re-set them.

### Pitfall 11: Wording drift
Strategy ids (`ny_n_break`, `ak_roxx_pro`) and words like "readiness", "gate", "stalled", "websocket" are jargon (Richard is non-technical, MEMORY: talk in plain language). Use "Live prices", "Option chain", "Option spreads", "Dhan live feed", "market closed", "last measured 1 Oct, 9:32 AM IST".

## Code Examples

### Hook (pattern of `useExitRecheck.ts`)
```ts
// Source: dashboard/src/hooks/useExitRecheck.ts:45-54 (same shape)
export function useDataHealth() {
  const poll = usePollMs(20_000)
  return useQuery({
    queryKey: ['data-health'],
    queryFn: () => api<DataHealth>('/api/data-health'),
    refetchInterval: poll,
    staleTime: 10_000,
    placeholderData: keepPreviousData,
  })
}
```

### Tile with token colour (no raw colour, mono/tabular via StatTile)
```tsx
// Source: StatTile.tsx props (label, value, sub, valueClass); tokens from index.css
const TONE: Record<Status, string> = {
  ok: 'text-[var(--up)]', slow: 'text-[var(--warn)]', stale: 'text-[var(--down)]',
  closed: 'text-slate-400', off: 'text-slate-400', none: 'text-slate-400',
}
<section className={cn(fx.panel, 'p-4')}>
  <h3 className="text-sm font-bold text-slate-100">Data health</h3>
  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
    <StatTile label="Live prices" value={word} sub={detail} valueClass={TONE[d.ticks.status]} />
    ...
```

### Missing endpoint renders a sentence, not an error box (Phase 3 house pattern)
```tsx
// Source: StrategyPerformancePage.tsx:144-147
{q.isError && !q.data ? <p className="text-[12.5px] text-slate-500">…may need a restart.</p> : ...}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Read tick/chain/spread health from logs or CONCERNS "Missing Critical Features: Market data debugging dashboard" (CONCERNS.md:140-143) | One read-only data-health endpoint + panel | This phase | Closes that concern |
| Go-live readiness chips with hover-only reasons (CryptoPanel.tsx:660-679) | Visible list with LIVE/PAPER + plain reason under the arm button | This phase | Closes the CONCERNS "Crypto pairs gate logic not documented in UI" bug |

**Deprecated/outdated:** `LiveReadinessRow` (replaced); the CONCERNS.md lines for those two items should be marked resolved at phase end (as 03-06 did for its concern).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Spread thresholds amber 10 min / red 30 min are reasonable (no measured cadence distribution for spread was taken; only per-day sample coverage) | Pattern 2 thresholds | Too tight: false amber/red during market hours; too loose: slow to flag. Easy to retune (constants in `data_health.py`) |
| A2 | 3-minute open-bell grace is enough for the first scan cycle (first chain rows observed 09:15:08-09:15:18 on 09-24/09-25/10-01) | Pitfall 4 | A few seconds of amber at 09:15; harmless |
| A3 | The chain rows written outside market hours come from non-scanner callers of `plan_instrument` (dashboard preview/strategy-lab paths) — only inferred from planner.py call sites, not traced | Pitfall 5 | None for this phase (neutral when closed); only affects the wording "Scanner" which we avoid |
| A4 | Spread-sample stoppages on 09-21/09-23/09-29/10-01 were server downtime/restarts, not a sampler bug (not investigated) | Pattern 2 evidence | Phase 4 only needs to display it; if it is a sampler bug it is a separate finding for Richard |
| A5 | Mounting the panel at the top of the Index Options page (rather than in the header) satisfies "on the main page, next to the status pills" (D-02) because the pills are in the header of every page and hidden below `sm` | Pattern 2 mount | If Richard meant a pop-over from the header pill, it is a small follow-up; flag at the end-of-phase human look |
| A6 | `mode=ro` read of a WAL database works while the server writes (it did this session with the server running) | Pattern 2 | A transient `OperationalError` is caught -> `none`; no harm |
| A7 | `ENABLE_TICK_FEED` current value in Richard's `.env` is unknown (`.env` is permission-blocked); the panel must handle both on and off | Pattern 2 | None if both states render |

## Open Questions (RESOLVED)

1. **Replace the old "Go-live readiness" block?**
   - What we know: it duplicates the new list with jargon and a `rounded-lg` drift, and only appears far below the arm button.
   - Recommendation: replace it (display only, reversible) and tell Richard in one plain sentence at the end-of-phase look. No decision needed up front.
2. **Header pill shares the new query?**
   - Recommendation: yes (one poll), keep the pill's wording; low risk. Planner may skip if it wants zero change to `StatusPills.tsx`.
3. **Panel placement wording (A5)** — default chosen; confirm during the manual look. No question for Richard now.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python / pytest | backend tests | yes | 3.14.2 / 9.0.3 | — |
| Node / npm | dashboard build, tsc, eslint | yes | v24.16.0 / 11.13.0 | — |
| `dashboard/node_modules` (tsc, eslint, vite) | build/typecheck/lint | yes (dir present) | — | `npm --prefix dashboard install` |
| Running local server | manual look | yes — `GET /api/health` answered `{"ok":true,...}` at 127.0.0.1:8000 | — | Start with `python -m uvicorn index_ai.server:app --port 8000` |
| Dashboard password | calling the API from here | not available to the agent (CLAUDE.md: ask Richard) | — | Tests use `TestClient(app)` (conftest freezes env) |
| Browser for the visual look | UIUX-03 manual check | playwright MCP is project-scoped per CLAUDE.md | — | Richard opens the dashboard |

**Missing dependencies with no fallback:** none. The server must be **restarted by Richard** for the new endpoints to exist.

## Validation Architecture

> `workflow.nyquist_validation` is `true` in `.planning/config.json`.

### Test Framework
| Property | Value |
|----------|-------|
| Backend framework | pytest 9.0.3, `testpaths = ["tests"]` [VERIFIED: pyproject.toml `[tool.pytest.ini_options]`] |
| Dashboard | **No JS test runner.** Verification = `npm --prefix dashboard run build` (runs `tsc -b && vite build`, package.json scripts) + `tsc --noEmit -p tsconfig.app.json` + `eslint` + manual look |
| Config file | `pyproject.toml`; `tests/conftest.py` (autouse: frozen env, throwaway `.env`, `trade_memory.sqlite`, `market_log.sqlite`, `spread_samples.jsonl`; Telegram vars cleared) |
| Quick run command | `python -m pytest tests/test_data_health.py tests/test_crypto_live_pairs.py tests/test_tick_feed.py -q` |
| Full suite command | `python -m pytest -q` (660 tests, ~7-8 min per CLAUDE.md) |
| Lint | `ruff check index_ai/` (~8 pre-existing errors; compare, don't chase zero) |
| Dashboard typecheck/lint | `Set-Location dashboard; npx tsc --noEmit -p tsconfig.app.json; npx eslint <touched files>` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UIUX-01 | View lists only pairs the lane visits (enabled strategies × `_symbols_for`); straddle and removed coins absent | unit | `python -m pytest tests/test_crypto_live_pairs.py -q` | extend existing (Wave 0) |
| UIUX-01 | `live` only when armed AND pair in `crypto_live_pairs()`; not armed -> nothing live but `eligible` kept | unit | same | Wave 0 |
| UIUX-01 | Zero-trade active pair included with plain reason; no "None"/"1 trades"/"$-" in reasons | unit | same | Wave 0 |
| UIUX-01 | `crypto_live_pairs` raising -> `read_ok False`, nothing live (mirrors lanes.py:407-408) | unit | same | Wave 0 |
| UIUX-01 | `GET /api/crypto/live-pairs` 200 + shape, read-only (GET only) | endpoint (TestClient) | `python -m pytest tests/test_data_health.py -q -k live_pairs` | Wave 0 |
| UIUX-02 | `classify`: closed market -> neutral for any age; ok/slow/stale boundaries at 60/300 (ticks), 300/600 (chain), 600/1800 (spread); grace caps at slow; None -> none | unit | `python -m pytest tests/test_data_health.py -q -k classify` | Wave 0 |
| UIUX-02 | Chain age via `?mode=ro` against a tmp DB (rows with `+05:30` ts) and missing DB -> `None` without creating a file | unit | `python -m pytest tests/test_data_health.py -q -k chain` | Wave 0 |
| UIUX-02 | Spread age from tail of a >64 KB tmp jsonl (last `at` per instrument; partial first line ignored); missing file -> None; patch `SKIPS_PATH` if `observe` is used | unit | `python -m pytest tests/test_data_health.py -q -k spread` | Wave 0 |
| UIUX-02 | Tick line from `tick_feed._state = FeedState(...)` monkeypatch: off / connected / stalled (pattern of tests/test_fast_trail_loop.py:68-71,108) | unit | `python -m pytest tests/test_data_health.py -q -k tick` | Wave 0 |
| UIUX-02 | `GET /api/data-health` 200, never raises when every source is missing; square-off window makes chain neutral | endpoint | `python -m pytest tests/test_data_health.py -q -k endpoint` | Wave 0 |
| UIUX-02 | Event-loop safety: handler uses `asyncio.to_thread` | static | `Select-String -Path index_ai/server.py -Pattern "data_health" -Context 0,4` (manual read) / grep in verify step | n/a |
| UIUX-03 | No raw colours / radius drift / hand-rolled panel / local Date in changed dashboard files | static grep (below) | see checklist | n/a |
| UIUX-03 | Build, typecheck, lint clean | build | `npm --prefix dashboard run build`; `npx tsc --noEmit -p tsconfig.app.json`; `npx eslint <files>` | n/a |

### UIUX-03 acceptance checklist (the reviewer's 7 checks as greppable criteria)
Run on the diff of `dashboard/src` (`git diff --name-only -- dashboard/src`); the reviewer file is `.claude/agents/ui-consistency-reviewer.md` (03-06 had no subagent available and applied it by hand in a table — do the same if needed, one row per check):
1. **Raw colours:** no Tailwind colour utility outside `slate`/`cyan`/`violet` and no hex/`rgb(` in new lines; semantic colours only via `var(--up|--down|--warn|--acc|--hair|--hair-soft|--panel)`. Red for stale = `--down` (not `--armed`).
2. **Radius:** no new `rounded-xl|2xl|3xl|lg` on panel-like containers (`rounded-md` panels; small chips use `rounded`/`rounded-full` as `LearningPanel`/`Pill` do). The old `rounded-lg` tiles in `LiveReadinessRow` go away with it. (`Button`'s own `rounded-lg`, Button.tsx:62, is the shared primitive's — not a finding.)
3. **Data is mono + tabular-nums:** every age, count, `$` figure and time carries `font-mono tabular-nums` (or comes via `StatTile`/`fx.cardValue`).
4. **Shared surfaces:** containers written as `cn(fx.panel, ...)` / `fx.card`, never the inline `rounded-md border border-[var(--hair)] bg-[var(--panel)]`.
5. **No local date logic:** no `new Date(`, `.toISOString(`, `.getDay(` in new lines; the server supplies seconds and an IST string.
6. **Layout math:** `AppShell` header (`h-[2.85rem]`), `TickerStrip` (`h-10`), `Sidebar` offsets untouched; mount is in the page body.
7. **No duplicate JSX:** uses `StatTile`/`fx.card`/`Button`; no new stat-tile or table clone; `LiveReadinessRow` removed rather than left as a twin.
Plus: plain-words check on visible strings (no "gate", "readiness", "ladder", "stalled", "websocket", raw strategy ids), phone-width look (grid collapses to 2 columns; no horizontal scroll).

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_data_health.py tests/test_crypto_live_pairs.py -q` (backend tasks) / `Set-Location dashboard; npx tsc --noEmit -p tsconfig.app.json` (dashboard tasks)
- **Per wave merge:** the quick command + `npm --prefix dashboard run build`
- **Phase gate:** full `python -m pytest -q` green, `ruff check index_ai/` no new errors vs `git stash` baseline, dashboard build + tsc + eslint clean, then `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_data_health.py` — classify, chain RO lookup, spread tail, tick states, both endpoints (UIUX-01 endpoint, UIUX-02)
- [ ] extend `tests/test_crypto_live_pairs.py` — view function parity cases (UIUX-01)
- [ ] No framework install needed (pytest present; no JS runner will be added — out of scope)

### Manual-only checks (human, end-of-phase per `human_verify_mode`)
- With the server restarted and the real password: Index Options page shows the Data Health panel; on a closed market (now: Saturday) every line is neutral "market closed" with the last-measured IST time and **nothing red**.
- Crypto tab: list sits right under the arm strip; Paper mode shows "would use real money if armed"; the real data today should show `cpr_trend` and `ak_roxx_pro` coins as eligible (both strategies are "ready" in today's real readiness: ak_roxx_pro 63 trades/19 days, cpr_trend 91 trades/18 days) and `ny_n_break` as not ready ("losing overall so far (−$143.70)"). Arming is **Richard's action only**; the check must not arm anything.
- Phone width (narrow window): both new blocks readable, no overflow; pills row is hidden there so the panel is the only view.
- Do **not** verify live behaviour by arming; the unarmed list is the full UIUX-01 check, and the armed wording is covered by the unit test.

## Security Domain

> `security_enforcement: true`, `security_asvs_level: 1`, `security_block_on: high` [VERIFIED: .planning/config.json].

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (no new auth) | Existing `DASHBOARD_PASSWORD` HTTP Basic gate covers every `/api` route including new ones (CLAUDE.md; tests/test_dashboard_password.py) |
| V3 Session Management | no | — |
| V4 Access Control | yes | GET-only, read-only endpoints; **not** behind `require_admin_secret` (that is for money-moving writes) but inside the dashboard password gate; no endpoint here can arm, place an order or change a stop |
| V5 Input Validation | yes (minimal) | No query/body parameters on either endpoint; SQL uses bound parameters (`WHERE session=? AND instrument=?`) and a constant DB path; instrument keys come from `configured_index_keys()` |
| V6 Cryptography | no | — |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Event-loop starvation (blocking SQLite/file I/O in `async def`) | Denial of service | `asyncio.to_thread` (CLAUDE.md: "has bitten twice") + indexed/RO queries only |
| Accidental write via a "read" path (`market_log.connect()` migrations) | Tampering | `file:...?mode=ro` URI |
| Information leak of secrets in the new payloads | Information disclosure | Return ages, status words, `last_error` (already truncated to 200 chars, tick_feed.py `[:200]`) only — no tokens, no `.env` values, no file paths |
| UI claiming LIVE when orders will not be real (or the reverse) | Spoofing / repudiation (operator trust) | Single source of truth + parity tests (Pattern 1) |
| Test run overwriting real state (spread_skips.json already polluted) | Tampering | Redirect `SKIPS_PATH` in any new test that touches `observe` |

### Trading-safety constraints (CLAUDE.md money path)
Nothing in this phase may touch `executor.py`, `dhan_orders.py`, `exit.py`, `config.py` arming (`arm_live_trading`/`set_trading_mode`), `charges.py`, `market_context/spread_calib.py` **behaviour**, `risk_manager.py`, `trailing.py`, `credit_spread.py`, or `scanner.py` tick stops. New code only **imports and reads**: `crypto.lanes._enabled_strategies/_symbols_for` (private helpers, read-only calls — **do not edit `lanes.py`**), `strategy_performance`, `tick_feed.status`, `market_clock`, and the market_log/spread files. A lock tripwire like Phase 3's (count of `threading.Lock()`/`RLock()` unchanged = 13 per 03-06-SUMMARY) is a cheap extra check that no concurrency primitive was added. `ruff check index_ai/` error count must not rise.

## Sources

### Primary (HIGH confidence — read or executed this session)
- `crypto/lanes.py` (115-160, 370-440, 570-630, 905-1030), `crypto/config.py:170-195`, `crypto/api.py:36-98`, `crypto/btc_straddle.py:1-60`, `crypto/executor.py` (`live_gate`)
- `index_ai/strategy_performance.py` (full), `index_ai/server.py` (160-220, 735-790, 1455-1620), `index_ai/tick_feed.py` (full), `index_ai/market_log.py` (1-170, 220-430), `index_ai/market_context/spread_calib.py` (full), `index_ai/daily_ops.py:50-113`, `index_ai/scanner.py` (385-415, 960-1030, `SCAN_INTERVAL_SECONDS = 90`), `index_ai/market_clock.py` (full), `index_ai/instruments.py:139-155`, `index_ai/dhan_auth.py:1022,1062`
- Dashboard: `App.tsx`, `AppShell.tsx`, `TickerStrip.tsx`, `StatusPills.tsx`, `CryptoPanel.tsx` (1-250, 440-480, 590-690), `CryptoExecutionPanel.tsx` (full), `StatTile.tsx`, `Button.tsx`, `PageHeader.tsx`, `theme.ts`, `ist.ts`, `usePageVisible.ts`, `api.ts`, `useExitRecheck.ts`, `cryptoFmt.ts`, `strategies.ts:25-90`, `StrategyPerformancePage.tsx:1-40, 105-215`, `index.css:1-135`
- `.claude/agents/ui-consistency-reviewer.md`, `.planning/phases/03-exit-optimisation/03-06-SUMMARY.md`, `.planning/codebase/CONCERNS.md`, `.planning/config.json`, `tests/conftest.py`, `tests/test_crypto_live_pairs.py`, `tests/test_crypto_phase4.py:95-360`
- Executed read-only against real data: `crypto_live_readiness()`/`crypto_live_pair_table()` output; SQLite `EXPLAIN QUERY PLAN` + timings on `memory/market_log.sqlite` (7,002,439,680 bytes) incl. the 31.9 s `ticks` scan; per-session chain cadence (25 Sep, 30 Sep, 1 Oct); `spread_samples.jsonl` per-day coverage; `GET /api/health`

### Secondary (MEDIUM)
- `CLAUDE.md` (test counts, restart/rebuild/password facts — project statements, not re-executed: 660 tests, ~7-8 min)

### Tertiary (LOW)
- none (no web research was needed; all findings are in-repo)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — nothing new; versions read from manifests/toolchain
- Architecture: HIGH — every decision point read in code and, for performance, measured on the real DB
- Pitfalls: HIGH for 1-4, 6-10 (read/measured); MEDIUM for 5 (cause of off-hours chain rows inferred)
- Thresholds for spread and grace: MEDIUM (A1/A2)

**Research date:** 2026-10-03
**Valid until:** ~30 days (stable code); re-check chain/spread cadence numbers if the scanner interval changes
