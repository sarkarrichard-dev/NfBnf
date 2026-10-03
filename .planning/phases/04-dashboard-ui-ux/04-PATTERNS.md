# Phase 4: Dashboard UI/UX - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 12 (7 new, 5 modified)
**Analogs found:** 12 / 12 (all analog paths verified git-tracked)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|---|---|---|---|---|
| `index_ai/strategy_performance.py` (add `crypto_live_pair_view`) | service (pure fn) | transform | same file `crypto_live_pair_table` (~L405-430) | exact |
| `index_ai/data_health.py` (NEW) | service | request-response (read-only collectors) | `index_ai/tick_feed.py` `status()` + RESEARCH recipes | role-match |
| `index_ai/server.py` (2 GET endpoints) | route | request-response | `server.py` `/api/crypto/live-readiness` (~L1576-1585), `/api/exit-recheck` (~L1598) | exact |
| `dashboard/src/hooks/useDataHealth.ts` (NEW) | hook | polling | `dashboard/src/hooks/useExitRecheck.ts` L45-54 | exact |
| `dashboard/src/hooks/useCryptoLivePairs.ts` (NEW) | hook | polling | `dashboard/src/hooks/useStrategyLearning.ts` | exact |
| `dashboard/src/components/DataHealthPanel.tsx` (NEW) | component | polling display | `ExitRecheckPanel` in `dashboard/src/components/pages/StrategyPerformancePage.tsx` L116-215 + `ui/StatTile.tsx` | exact |
| `dashboard/src/components/CryptoLivePairs.tsx` (NEW) | component | polling display | same ExitRecheckPanel list markup | role-match |
| `dashboard/src/components/CryptoExecutionPanel.tsx` (mount) | component | - | itself, arm strip L236-265 | exact |
| `dashboard/src/components/CryptoPanel.tsx` (remove LiveReadinessRow) | component | - | itself (L125-145, 178-183, 461-466, 614-685) | exact |
| `dashboard/src/App.tsx` (mount) | component | - | itself, `tab === 'trade'` block L229-249 | exact |
| `tests/test_data_health.py` (NEW) | test | request-response | `tests/test_admin_auth.py` (TestClient) + `tests/test_fast_trail_loop.py:68-71,108` | exact |
| `tests/test_crypto_live_pairs.py` (extend) | test | transform | itself L1-40 | exact |

Note: the file is under `dashboard/src/components/pages/`, not `components/` directly (CONTEXT said "StrategyPerformancePage.tsx" without a path). `PageHeader` is `dashboard/src/components/shell/PageHeader.tsx`. Lane code is root `crypto/lanes.py` (tracked), not under `index_ai/`.

## Pattern Assignments

### Endpoints in `index_ai/server.py` (route, request-response)

**Analog:** `/api/crypto/live-readiness` (server.py ~L1576-1585) and `/api/exit-recheck` (~L1598-1604). Place the new routes beside them.

```python
@app.get("/api/crypto/live-readiness", include_in_schema=False)
async def crypto_live_readiness_api() -> dict[str, Any]:
    """docstring ..."""
    from index_ai.strategy_performance import crypto_live_pair_table, crypto_live_readiness

    def _read() -> dict[str, Any]:
        return {"strategies": crypto_live_readiness(), "pairs": crypto_live_pair_table()}

    return await asyncio.to_thread(_read)
```
New live-pairs: same shape; inside `_read()` import `crypto.lanes._enabled_strategies/_symbols_for`, `crypto.config` settings (`crypto_settings().live_orders_enabled`), build `active = {(st, sym) for st in _enabled_strategies(s) for sym in _symbols_for(st, s)}`, return `crypto_live_pair_view(active, armed)`. Check exact import names of `crypto_settings` with `grep -n "def crypto_settings\|_enabled_strategies\|def _symbols_for" crypto/*.py` before writing.
New data-health: 3 lines, `from index_ai.data_health import collect; return await asyncio.to_thread(collect)`. GET only, no params, NOT behind `require_admin_secret` (gate applies only to POST arming routes). Do not copy `/api/tick-feed` (L1465-1470): it is `async def` with no thread, fine only because it does no I/O.

### `index_ai/strategy_performance.py::crypto_live_pair_view` (pure transform)

**Analog:** `crypto_live_pair_table` (~L405-430) in the same file. Copy its reason ladder, then fix the findings from RESEARCH:
```python
ready = {r["strategy"]: r for r in crypto_live_readiness()}
allowed = crypto_live_pairs()   # call via module global so monkeypatch of sp.crypto_live_pairs works
...
why = (None if pair in allowed
       else f"strategy not ready: {strat.get('why_not')}" if not strat.get("ready")
       else f"only {row['trades']} trades on this coin (needs {CRYPTO_PAIR_MIN_TRADES})" if row["trades"] < CRYPTO_PAIR_MIN_TRADES
       else f"losing on this coin (${row['net']:.2f})")
```
Changes: iterate `active` (left-join onto table rows keyed `(strategy, coin)`, zero-trade -> "no trades on this coin yet"); `live = armed and pair in allowed`; keep `eligible`; fix plural ("only 1 trade"), money sign (`-$12.30`), and `strat.get('why_not')` None case. Keep substrings `losing on this coin`, `only 3 trades`, `strategy not ready` (existing test asserts them). Wrap `crypto_live_pairs()` in try/except -> `read_ok False`, nothing live (mirrors lanes.py L407-408). Return `{"armed","read_ok","pairs":[{strategy,coin,trades,net_usd,eligible,live,reason}]}`. Live rule source: `crypto/lanes.py:599` `live=live and (strat, sym) in live_pairs`.

### `index_ai/data_health.py` (NEW, service)

No exact analog; compose from:
- Tick line: `from index_ai import tick_feed; tick_feed.status()` -> keys `enabled, connected, ticks, reconnects, seconds_since_last_tick, stalled, last_error` (index_ai/tick_feed.py ~L141-155). Read via module attr at call time (test monkeypatches `tick_feed._state`).
- Chain line: read-only URI (RESEARCH recipe):
```python
uri = market_log.DB_PATH.resolve().as_uri() + "?mode=ro"   # read DB_PATH at CALL time
db = sqlite3.connect(uri, uri=True, timeout=2)
sess = db.execute("SELECT MAX(session) FROM chain").fetchone()[0]
db.execute("SELECT MAX(ts) FROM chain WHERE session=? AND instrument=?", (sess, key))
```
  Never `market_log.connect()`, never query `ticks`, never `COUNT(*)`.
- Spread line: tail 64 KB of `spread_calib.SAMPLES_PATH` (call-time attribute), drop first partial line, `json.loads`, last `at` per `instrument`. Never `spread_calib.summary()/status()`.
- Clock: `market_clock.is_market_open()`, `is_square_off_window()`, `parse_ist_datetime`, `now_ist()`, `session_times()["market_open"]` for the 3-min grace. Indices: `instruments.configured_index_keys()`.
- Pure `classify(age, market_open, amber, red, *, in_grace=False, paused=False)` exactly as RESEARCH; thresholds constants: ticks 60/300, chain 300/600, spread 600/1800. Chain forced `closed` when square-off window. `collect()` never raises (per-source try/except -> `status:"none"`).

### `dashboard/src/hooks/useDataHealth.ts` and `useCryptoLivePairs.ts` (hook)

**Analog:** `dashboard/src/hooks/useExitRecheck.ts` L1-5 and L45-54:
```ts
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'
// types exported at top, then:
export function useExitRecheck(enabled: boolean) {
  const poll = usePollMs(60_000, enabled)
  return useQuery({
    queryKey: ['exit-recheck'],
    queryFn: () => api<ExitRecheck>('/api/exit-recheck'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
```
`useDataHealth`: `usePollMs(20_000)`, key `['data-health']`, `staleTime: 10_000`. `useCryptoLivePairs`: `usePollMs(60_000)`, key `['crypto-live-pairs']`. `useStrategyLearning.ts` shows the same file layout (types then hook); no mutations needed (read-only).

### `dashboard/src/components/DataHealthPanel.tsx` (component)

**Analog:** `ExitRecheckPanel` (`components/pages/StrategyPerformancePage.tsx` L116-215) + `components/ui/StatTile.tsx` (props `label, value, sub?, valueClass?, points?`).
Imports (copy style; one level up from `components/`): `import { cn } from '../lib/cn'`, `import { fx } from '../lib/theme'`, `import { StatTile } from './ui/StatTile'`, hook from `../hooks/useDataHealth`.
Container + states (verbatim house pattern):
```tsx
<section className={cn(fx.panel, 'p-4')}>
  <h3 className="text-sm font-bold text-slate-100">Data health</h3>
  {q.isError && !d ? (
    <p className="text-[12.5px] text-slate-500">…not available yet — the app may need a restart.</p>
  ) : !d ? (<p className="text-[12.5px] text-slate-500">Loading…</p>) : (
    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4"> <StatTile .../> x4 </div>
  )}
</section>
```
Tone map: `ok -> text-[var(--up)]`, `slow -> text-[var(--warn)]`, `stale -> text-[var(--down)]`, `closed|off|none -> text-slate-400` (use `--down`, never `--armed`). Sub-line uses server-formatted `last_at` IST string; no `new Date(`. Plain words (no "websocket", "stalled", "gate").

### `dashboard/src/components/CryptoLivePairs.tsx` (component)

**Analog:** ExitRecheckPanel list markup (L150-205): `<ul className="space-y-2">`, `<li className="text-[12.5px]">`, chip `rounded px-1.5 py-0.5 font-mono text-[10px] uppercase` with `bg-[var(--up)]/10 text-[var(--up)]` for LIVE and slate for PAPER, detail line `pl-3 font-mono text-[11px] tabular-nums text-slate-500`, reason `text-[11.5px] text-slate-500`. Since it is mounted inside CryptoExecutionPanel's existing `fx.panel`, wrap in `<div className="mt-3 border-t border-[var(--hair)] pt-3">` rather than a second panel. Helpers: `STRATEGIES.find(s => s.id === id)?.name ?? id` (`lib/strategies.ts:29-33`), `coin.replace(/USD.?$/, '')` (CryptoPanel.tsx:675), `usd`/`pnlCls` from `lib/cryptoFmt.ts`. Group by strategy; print shared reason once. Headings: not armed "If you arm: which pairs would use real money"; armed "Right now: which pairs use real money".

### `CryptoExecutionPanel.tsx` (mount)

Insert `<CryptoLivePairs />` immediately after the arm-strip block `{isLive ? (<div ...>...</div>) : null}` (ends L265), before the kill-switch `<dl>` (L267). Existing imports pattern at L1-8 (add `import { CryptoLivePairs } from './CryptoLivePairs'`). Do not use `armed = !!s?.live_armed` (L68) for the list; the server supplies `armed`.

### `CryptoPanel.tsx` (remove)

Delete `LiveReadinessRow` (L614-685, has `rounded-lg` at L636), its `readiness` query (L178-183), its mount (L461-466), and unused types (L125-145); then check for now-unused imports (`tsc`/`eslint` will flag). Leave `/api/crypto/live-readiness` backend untouched.

### `App.tsx` (mount)

In `tab === 'trade'` (L229-249): add `<DataHealthPanel />` between `<PageHeader ... />` (L232-236) and `<StatsOverview` (L237); add to imports near L5/L19. Do not touch `AppShell` header (`h-[2.85rem]`), `TickerStrip` (`h-10`), or Sidebar offsets (reviewer check 6). `StatusPills.tsx` change optional (title text / share `['data-health']` key); if touched do not add `new Date(`.

### `tests/test_data_health.py` (NEW)

**Analog 1:** `tests/test_admin_auth.py` L1-12 TestClient pattern:
```python
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from index_ai.server import app
client = TestClient(app)
# ...
assert client.get("/api/health").status_code == 200
```
Use `client.get("/api/data-health")` and `client.get("/api/crypto/live-pairs")`: assert 200 + shape; assert never raises with all sources missing; assert POST -> 405.
**Analog 2:** `tests/test_fast_trail_loop.py` L68-71, L108:
```python
return tick_feed.FeedState(connected=False, last_tick_at=time.monotonic() - 600)
monkeypatch.setattr(tick_feed, "_state", _down_feed_state())   # status()["stalled"] is True
```
Also `FeedState(connected=True, last_tick_at=time.monotonic())` (L148) and `FeedState()` (L150) for ok / off states.
Chain test: build tmp sqlite with a `chain(session, instrument, ts, ...)` table and `idx_chain_session`, set `monkeypatch.setattr(market_log, "DB_PATH", tmp)`; missing DB -> age None and file not created. Spread test: write >64 KB jsonl at `tmp_path`, `monkeypatch.setattr(spread_calib, "SAMPLES_PATH", ...)`; if calling `spread_calib.observe` also patch `SKIPS_PATH` (not redirected by conftest). Classify tests are plain asserts on boundaries 60/300, 300/600, 600/1800, closed-market neutral, grace cap, None.
For market state, monkeypatch `market_clock.is_market_open` / `is_square_off_window` (patch on the module the new code calls them through).

### `tests/test_crypto_live_pairs.py` (extend)

**Analog:** its own helpers L1-22:
```python
def _journal(tmp_path, monkeypatch, rows):   # writes crypto_journal.jsonl, patches crypto.journal.JOURNAL_PATH and sp.data_epoch -> None
def _trades(strategy, asset, n, pnl, start=date(2026, 9, 1)): ...
```
Add cases calling `sp.crypto_live_pair_view(active, armed)`: not armed -> none `live`, `eligible` kept; armed + ready strategy (30 trades/16 days) + profitable coin -> live; pair not in `active` (straddle, PAXGUSD) absent; zero-trade active pair -> reason "no trades on this coin yet"; no "None"/"1 trades" in any reason; `monkeypatch.setattr(sp, "crypto_live_pairs", boom)` -> `read_ok False`. Also `tests/test_crypto_phase4.py:212-214` patches `index_ai.strategy_performance.crypto_live_pairs` by string path, which the view must honour (call via module global). Parity test: assert `active` helper builds pairs the same way as lanes.py L427-428.

## Shared Patterns

### Non-blocking read-only endpoints
**Source:** server.py L1576-1604. Every handler `async def` + `await asyncio.to_thread(fn)`; no params; `include_in_schema=False`; docstring says "Read-only".

### Conftest redirects tests rely on (tests/conftest.py ~L30-65)
Autouse fixture: clears `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (never re-set); redirects `index_ai.config.ENV_PATH`, `index_ai.config.DB_PATH`, `index_ai.learning.DB_PATH` to tmp; `index_ai.market_log.DB_PATH` -> `tmp_path/market_log.sqlite`; `spread_calib.SAMPLES_PATH` -> `tmp_path/spread_samples.jsonl`; `charges._measured_cache.clear()`. NOT redirected: `spread_calib.SKIPS_PATH`, `crypto.journal.JOURNAL_PATH` (patch per test via `_journal`). New code must read `market_log.DB_PATH` and `SAMPLES_PATH` at call time (not import-time constants) so these redirects apply.

### Dashboard UIUX-03 bar
Panels `cn(fx.panel, 'p-4')` (`lib/theme.ts` L6-12: `panel`, `card`, `cardLabel`, `cardValue`), tiles via `StatTile`, chips `rounded px-1.5 py-0.5 font-mono text-[10px]`, tokens only (`--up --down --warn --acc --hair`), numbers mono + tabular-nums, no `rounded-lg/xl`, no `new Date(`, no local date math, plain words. Unreachable endpoint renders a sentence, not an error box (StrategyPerformancePage L144-147). Finish with `npm --prefix dashboard run build`.

## No Analog Found

| File | Role | Reason |
|---|---|---|
| read-only SQLite `?mode=ro` chain lookup (inside `data_health.py`) | service | No existing read-only connection helper; `market_log.connect()` is write-capable. Use RESEARCH verified recipe |
| spread-file tail read | service | `spread_calib._samples()` reads the whole 17 MB file; write a small tail reader |

## Metadata

**Analog search scope:** `index_ai/server.py`, `index_ai/strategy_performance.py`, `crypto/`, `dashboard/src/{hooks,components,lib,App.tsx}`, `tests/`
**Pattern extraction date:** 2026-10-03
