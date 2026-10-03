# Phase 3: Exit Optimisation - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 15 (4 new, 6 edited, 2 deleted, 3 comment/doc-only)
**Analogs found:** 14 / 15 (no analog: India replay driver beyond reuse of strategy_lab helpers)

All cited paths were confirmed git-tracked (`git ls-files`). No `.claude/worktrees/` or `graphify-out/` paths used.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|---|---|---|---|---|
| `index_ai/exit_recheck.py` (NEW) | service (stats) | batch / transform | `index_ai/strategy_learning.py` + `index_ai/trade_lots.py` (store) + `index_ai/reconcile.py` (alert) | role-match |
| `index_ai/daily_ops.py` (EDIT, 1 step) | job | batch | same file, lines 229-234 | exact |
| `index_ai/server.py` (EDIT, 2 endpoints) | route | request-response | same file `/api/strategy-learning` (1588) + `POST /api/settings/features` (1427) | exact |
| `index_ai/position_exits.py` (EDIT) | utility | transform | itself (lines 25-37, 96) | exact |
| `index_ai/trailing.py` (EDIT, delete branch) | service | request-response | itself (232-259) | exact |
| `index_ai/entry_guard.py` (EDIT docstring) | doc | n/a | n/a | n/a |
| `index_ai/premium_trail.py`, `tests/test_premium_trail.py` (DELETE) | n/a | n/a | n/a | n/a |
| `tests/test_position_exits.py` (EDIT, rename) | test | n/a | itself lines 67-78 | exact |
| `dashboard/src/hooks/useExitRecheck.ts` (NEW) | hook | polling + mutation | `useStrategyLearning.ts` + `FeaturesPanel.tsx` | exact |
| `dashboard/src/components/pages/StrategyPerformancePage.tsx` (EDIT) | component | display | `LearningPanel` (same file, lines 14-98) | exact |
| `tests/test_exit_recheck.py` (NEW) | test | n/a | `test_strategy_learning.py`, `test_strategy_performance.py`, `test_reconcile_fault_injection.py`, `test_strategy_lab.py`, `test_admin_auth.py` | role-match |

## Pattern Assignments

### `index_ai/exit_recheck.py` (service, batch)

**(a) Stats module shape** - analog `index_ai/strategy_learning.py` lines 28-64. Import the gate, do not copy it:
```python
from index_ai.data_epoch import data_epoch
from index_ai.market_clock import now_ist_iso
from index_ai.strategy_performance import _after_epoch

WATCH_MAX = 15
OBSERVE_MAX = 40
READY_MIN_DAYS = 15            # NOTE: 15, not D-03's 14 (open question for planner)
FREEZE_LOOKBACK = 20
def _state(n_trades, n_days): ...   # watching / observing / ready
def _frozen(pnls_recent): return len(pnls_recent) >= 5 and sum(pnls_recent) > 0
```
Use: `from index_ai.strategy_learning import _state, _frozen, OBSERVE_MAX, READY_MIN_DAYS, FREEZE_LOOKBACK`. `_frozen` wants newest-first pnls: `_frozen(pnls[:FREEZE_LOOKBACK])`. Suggest only when `state == "ready" and not frozen`.

India exit-reason read-back: reuse `index_ai/day_review.py` lines 26-40 (`_exit_notes()` returns `{trade_id: note}` with the ` @ date, time IST` tail stripped by `_TS_TAIL`). Do not re-query `feedback`.

**(b) learned_settings upsert + lock** - `index_ai/trade_lots.py` imports (lines 5-8, 15): `json`, `threading`, `from index_ai.learning import connect, now_utc`. Read (lines 33-39) and write (lines 60-72):
```python
SETTINGS_KEY = "trade_lots"
def _load_stored_lots():
    with connect() as db:
        row = db.execute("SELECT value_json FROM learned_settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    if not row: return None
    try: data = json.loads(row["value_json"]) ...
    except (TypeError, ValueError, json.JSONDecodeError): return None

def set_lots_per_trade(lots):
    payload = {"lots_per_trade": value, "updated_at": now_utc()}
    with connect() as db:
        db.execute(
            """
            INSERT INTO learned_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (SETTINGS_KEY, json.dumps(payload), now_utc()),
        )
# line 80:
_LOTS_LOCK = threading.Lock()   # wrap whole read-modify-write (line ~86 `with _LOTS_LOCK:`)
```
Copy: key `"exit_recheck_baseline"`, ONE row holding `{segments: {seg: {trail_hit_rate, win_rate, n, distance, captured_at, drifted}}}`; hold a module-level `_RECHECK_LOCK` around the whole recompute-and-store; store absolute values.

**(c) Alert with de-dup key in try/except** - `index_ai/reconcile.py` lines 123-140 (lazy import inside try, swallow all):
```python
    try:
        from index_ai import notify

        notify.alert(text, key=f"reconcile:{sid}:{issue['kind']}")
    except Exception:
        pass
```
Copy with `key=f"exit-drift:{segment}:{baseline_captured_at}"`; fire only on persisted OK->DRIFT transition (notify stamp TTL is 3 days, `notify.py:36`). Text style: leading emoji + plain language + "Nothing was changed".

**(e) Read access to market_log** - there is NO read-only opener: `index_ai/market_log.py:57-69` `connect(path=None)` is a contextmanager that sets WAL + runs `_migrate` + commits (row_factory = sqlite3.Row). `strategy_lab.py` uses it directly (`from index_ai import candle_cache, market_log`, line 44; `with market_log.connect() as db:` lines 308, 676). Copy that; reads only. Replay helpers to import: `strategy_lab._index_path / _trail_hit / _fill / _quotes / _exit_prices`, `charges.leg_charge_rupees`. Wrap the whole replay in try/except and return "could not replay" (Pitfalls 5 and 6 in RESEARCH).

Journals: crypto `crypto.journal.JOURNAL_PATH` (`crypto/journal.py:18`, read at call time via module attribute so tests can monkeypatch); commodities `commodities.lanes.JOURNAL_PATH` (`commodities/lanes.py:39`). Always reference as module attribute (`journal.JOURNAL_PATH`) not `from ... import JOURNAL_PATH`, or monkeypatching will not take.

**Classifier skeleton:** see RESEARCH.md "Q2" code block (ordered regex tuples, unknown -> "other"). Test with the verbatim real strings in the Q2 table.

---

### `index_ai/daily_ops.py` (job, batch) - edit `run_eod()` after line 234

**(d) Exact existing step style** (lines 229-234, inside `run_eod`, right before the `cloud_backup` step at ~236):
```python
    try:  # daily snapshot of the confidence ladder so its trend is visible
        from index_ai.strategy_learning import learning_report

        report["strategy_learning"] = learning_report()
    except Exception as exc:
        report["strategy_learning"] = {"error": str(exc)[:200]}
```
Add the same shape:
```python
    try:  # suggest-only stop-distance re-check + drift watch (Phase 3)
        from index_ai.exit_recheck import run_recheck

        report["exit_recheck"] = run_recheck()
    except Exception as exc:
        report["exit_recheck"] = {"error": str(exc)[:200]}
```
`run_eod` is already offloaded via `asyncio.to_thread` (scanner `_run_eod_if_due` / `_eod_catch_up`), so the step itself stays synchronous. Place it before the backup step so the report/backup includes it. Do not call `notify` outside `run_recheck`'s own try/except.

---

### `index_ai/server.py` (route, request-response)

**GET analog** (lines 1588-1595, `/api/strategy-learning`; same shape at 1567-1575):
```python
@app.get("/api/strategy-learning", include_in_schema=False)
async def strategy_learning_api() -> dict[str, Any]:
    """... Read-only."""
    from index_ai.strategy_learning import learning_report

    return await asyncio.to_thread(learning_report)
```
**Non-financial POST analog** (lines 1427-1436, `POST /api/settings/features`):
```python
@app.post("/api/settings/features", include_in_schema=False)
async def update_feature(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    flag, on = ..., ...
    try:
        result = await asyncio.to_thread(set_feature_flag, flag, on)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
```
New: `@app.get("/api/exit-recheck", include_in_schema=False)` returning the last stored result (cheap, still `to_thread`) and `@app.post("/api/exit-recheck/run", include_in_schema=False)` -> `await asyncio.to_thread(run_recheck)`. No `require_admin_secret` (it is suggest-only, writes only the baseline row); the global `DASHBOARD_PASSWORD` middleware already covers it. Lazy-import `exit_recheck` inside the handler like the neighbours.

---

### `index_ai/position_exits.py` (EDIT - money-adjacent; trading-safety-reviewer applies)

**Current code to replace** (lines ~25-37; call at ~96):
```python
# Only the two-leg directional verticals — premium_trail watches a single short
# leg, so a two-sided structure (iron condor) still needs its regime close.
_TRAILED_VERTICALS = frozenset(
    {"SELL_BEAR_CALL_SPREAD", "SELL_BULL_PUT_SPREAD", "SELL_ATM_CALL", "SELL_ATM_PUT"}
)

def _premium_trailed_credit(trade: dict[str, Any]) -> bool:
    from index_ai.premium_trail import premium_trail_enabled

    action = str(trade.get("action") or (trade.get("signal") or {}).get("action") or "").upper()
    inst = str(trade.get("instrument") or (trade.get("option") or {}).get("instrument") or "")
    return action in _TRAILED_VERTICALS and premium_trail_enabled(inst)
...
    if _premium_trailed_credit(trade):      # ~line 96, inside strategy_exit_reason
        return None
```
**Replacement** (behaviour-identical; see RESEARCH Pattern 1): rename to `_index_trailed_credit`, import `SELL_TRAIL_POINTS` lazily from `index_ai.strategies.credit_spread`, return `action in _TRAILED_VERTICALS and inst.strip().upper() in SELL_TRAIL_POINTS`. Update the call at ~96, the comment above `_TRAILED_VERTICALS`, and the docstring at ~lines 85-90 (the "quarter-premium target... hard stop" text is stale; the owner is the 1:1 index trail in `credit_spread.evaluate_credit_open_trade`). FINNIFTY / empty instrument must still NOT be suppressed.

### `index_ai/trailing.py` (EDIT - delete premium branch)

Delete from the comment "# Premium trail on the option price..." through the end of the `if premium_trail_enabled(...)` block (lines ~232-259), including `from index_ai.premium_trail import (...)` and `pt_evaluated = False`. Keep and dedent-fix the fallback:
```python
# BEFORE
    if not pt_evaluated and mtm is not None:
        # legacy index (SENSEX) or a tick with no option quote — keep the rupee
        # profit-giveback trail as the profit protection.
        from index_ai.profit_trail import evaluate_profit_trail

        updated, profit_hit, profit_reason = evaluate_profit_trail(updated, float(mtm))
# AFTER
    if mtm is not None:   # D-05: rupee profit_trail fallback stays
        ...same body...
```
`_is_long_premium` (line ~176) may become unused in this function - check with grep before removing (it is still defined at module level; leave it unless ruff flags the import/local). Check `pivot_target` / `tx` locals do not become unused-variable ruff additions. Run `tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_profit_trail.py tests/test_credit_spread.py tests/test_position_exits.py tests/test_position_exits_ema.py`.

### `tests/test_position_exits.py` - rename test at lines ~67-78 to `test_index_trailed_credit_ignores_signal_flip`; update "premium_trail" comments; assertions unchanged.

### `index_ai/entry_guard.py` - docstring lines ~18-19 only.

---

### `dashboard/src/hooks/useExitRecheck.ts` (hook, polling + mutation)

**Analog `useStrategyLearning.ts`** (whole file, copy shape):
```ts
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

export function useStrategyLearning(enabled: boolean) {
  const poll = usePollMs(60_000, enabled)
  return useQuery({
    queryKey: ['strategy-learning'],
    queryFn: () => api<StrategyLearning>('/api/strategy-learning'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
```
Add `useRunExitRecheck()` using the **FeaturesPanel mutation shape** (`dashboard/src/components/FeaturesPanel.tsx` lines 4-5, 29-41):
```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
const qc = useQueryClient()
const run = useMutation({
  mutationFn: () => api('/api/exit-recheck/run', { method: 'POST' }),
  onSuccess: () => { toast.success('Re-check done'); void qc.invalidateQueries({ queryKey: ['exit-recheck'] }) },
  onError: (e: Error) => toast.error(e.message),
})
```
Export the row type with: `segment`, `venue`, `trades`, `trading_days`, `state: 'watching'|'observing'|'ready'`, `frozen`, `win_rate`, `trail_hit_rate`, `net`, `verdict`/`suggestion: string|null`, `drift: boolean`, `baseline`.

### `StrategyPerformancePage.tsx` - add `ExitRecheckPanel` (component)

Analog `LearningPanel` in the same file. Imports (lines 1-12) to extend: `cn` from `../../lib/cn`, `fx` from `../../lib/theme`; add `import { Button } from '../ui/Button'` and the new hook. Pill map (lines 14-18, REUSE `STATE_STYLE`, do not redefine):
```tsx
const STATE_STYLE: Record<string, string> = {
  watching: 'bg-white/[0.05] text-slate-400',
  observing: 'bg-[var(--warn)]/15 text-[var(--warn)]',
  ready: 'bg-[var(--up)]/15 text-[var(--up)]',
}
```
Panel/pill JSX (lines 32-62):
```tsx
<section className={cn(fx.panel, 'p-4')}>
  <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
    <h3 className="text-sm font-bold text-slate-100">Learning</h3>
    <span className="font-mono text-[11px] text-slate-500">...</span>
  </div>
  <ul className="space-y-2">
    <li className="text-[12.5px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-slate-200">{r.strategy} · {r.instrument}</span>
        <span className={cn('rounded px-1.5 py-0.5 font-mono text-[10px] uppercase', STATE_STYLE[r.state] || STATE_STYLE.watching)}>{r.state}</span>
        {r.frozen ? (<span className="rounded bg-[var(--up)]/10 px-1.5 py-0.5 font-mono text-[10px] text-[var(--up)]">frozen · working</span>) : null}
        <span className="font-mono text-[11px] text-slate-500">{r.trades} trades · {r.trading_days}d</span>
      </div>
      <p className="mt-0.5 pl-3 text-[11.5px] text-slate-500">{r.next_step}</p>
    </li>
  </ul>
  <p className="mt-3 border-t border-[var(--hair)] pt-2 text-[11px] leading-relaxed text-slate-500">footer</p>
</section>
```
Drift flag: copy the `down` token pill style already used on this page's error box (`border-[var(--down)]/40 bg-[var(--down)]/10 text-[var(--down)]`, line ~463) e.g. `rounded bg-[var(--down)]/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--down)]` "drift". Button: `<Button variant="secondary" pending={run.isPending} onClick={() => run.mutate()}>Re-check now</Button>` (props `variant`, `pending` as in FeaturesPanel lines ~70-75). Mount it in `StrategyPerformancePage` (line ~472) right after `<LearningPanel />`. `fx.*` tokens used on this page: `fx.panel` (others, e.g. `fx.card/cardLabel/cardValue`, appear in the same file's lower sections). Follow LearningPanel's `if (!d) return null`. Run `npm --prefix dashboard run build` and `npx tsc --noEmit`; ui-consistency-reviewer applies. Also edit `dashboard/src/lib/strategies.ts:196` wording (premium-trail text).

---

### `tests/test_exit_recheck.py` (test)

**conftest.py autouse `_test_env` (read in full) - what IS isolated per test:**
- env: EMA/CPR/etc vars set; `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` deleted (so `notify.send` is a no-op; still monkeypatch `index_ai.notify.alert` to assert).
- `.env` path -> tmp (`index_ai.config.ENV_PATH`, `strategy_params.ENV_PATH`).
- SQLite journal: `index_ai.config.DB_PATH` and `index_ai.learning.DB_PATH` -> `tmp_path/trade_memory.sqlite`, schema re-init flag reset. So the `learned_settings` baseline row IS isolated.
- `index_ai.market_log.DB_PATH` -> `tmp_path/market_log.sqlite`.
- `spread_calib.SAMPLES_PATH` -> tmp, `charges._measured_cache` cleared.
- strategy params reloaded.

**NOT isolated (monkeypatch explicitly):** crypto journal (`"crypto.journal.JOURNAL_PATH"`), commodities journal (`"commodities.lanes.JOURNAL_PATH"`), `data_epoch` (`monkeypatch.setattr(sp, "data_epoch", lambda: None)` where `sp` is `index_ai.strategy_performance`; also `strategy_learning` imports `data_epoch` by name, so patch `index_ai.strategy_learning.data_epoch` too if the new module imports via that route), any other `memory/*.json` state, `memory/.notify_seen.json` stamp file (avoid by patching `notify.alert`), and `index_ai/notify` itself (only token env is cleared).

**Patterns to copy:**
- Alert capture (`tests/test_reconcile_fault_injection.py` lines 308-320):
```python
alerts: list[tuple[str, str]] = []
monkeypatch.setattr("index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key)))
...
assert len(alerts) == 1
```
 Run the recheck twice with drifted data and assert still 1 alert (transition-only); also test `index_ai.notify.send` patch variant (lines 122-123).
- India trades (`tests/test_strategy_performance.py` lines 62-66, 202-205): `monkeypatch.setattr(sp, "data_epoch", lambda: None)`; `monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)` with dict trades; exit notes - patch `index_ai.day_review._exit_notes` to return `{id: note}` using verbatim strings from RESEARCH Q2 (or insert `feedback` rows via `index_ai.learning.connect()` since DB is tmp).
- Crypto journal (`tests/test_strategy_performance.py` lines 120-157): `j = tmp_path / "j.jsonl"`; write JSON lines; `monkeypatch.setattr("crypto.journal.JOURNAL_PATH", j)`. Do the same for commodities with `commodities.lanes.JOURNAL_PATH`.
- tmp market_log (`tests/test_strategy_lab.py` lines 8-16, 54-57):
```python
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(market_log, "DB_PATH", tmp_path / "m.sqlite")
    monkeypatch.setenv("ENABLE_MARKET_LOG", "true")
...
with market_log.connect() as con:
    con.executemany("INSERT INTO chain (ts, session, instrument, expiry, spot, strike, opt_type, oi, ltp, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
```
 (conftest already redirects DB_PATH; the fixture is only needed for `ENABLE_MARKET_LOG`.)
- Endpoint tests (`tests/test_admin_auth.py` lines 4, 9): `from fastapi.testclient import TestClient; from index_ai.server import app; client = TestClient(app)`; no password is set under test, so call `client.get("/api/exit-recheck")` and `client.post("/api/exit-recheck/run")` directly.
- Gate test: assert `n<40` or `days<15` -> `state != "ready"` and `suggestion is None`; frozen -> no suggestion; drift only at n>=40 and >15pp (`strategy_learning._state` boundaries in `tests/test_strategy_learning.py`).
- Deletion regression: `importlib.util.find_spec("index_ai.premium_trail") is None` plus source scan excluding `.claude/worktrees` and `graphify-out`.
- test-isolation-reviewer applies (anything reaching `notify` or writing state).

## Shared Patterns

### Blocking I/O off the event loop
**Source:** `index_ai/server.py` 1567-1595. Every new handler is `async def` + `await asyncio.to_thread(fn)`; import the module lazily inside the handler.

### Never-raise side effects
**Source:** `index_ai/reconcile.py` 123-140 and `daily_ops.py` 229-234: lazy import inside `try`, `except Exception` swallow (alert) or `{"error": str(exc)[:200]}` (report step).

### Shared-state writes
**Source:** `index_ai/trade_lots.py` 60-80: `learned_settings` upsert via `learning.connect()`, module `threading.Lock`, absolute values.

### Confidence ladder
**Source:** `index_ai/strategy_learning.py` 34-64. Import `_state`, `_frozen`, constants; frozen is checked before any suggestion; suggest != apply.

## No Analog Found

| File | Role | Reason |
|---|---|---|
| India alt-distance replay driver in `exit_recheck.py` | service | `strategy_lab.run_session` replays lab candidates, not journal trades; only helpers (`_index_path`, `_trail_hit`, `_fill`, `_quotes`, `_exit_prices`) + `leg_charge_rupees` reuse. Needs a new thin driver; use RESEARCH Q3 limits and Pitfalls 5-6. |

## Notes for planner

- No read-only SQLite opener exists; `market_log.connect()` runs WAL + `_migrate` + commit (harmless but not strictly read-only).
- `READY_MIN_DAYS` is 15 in code vs 14 in D-03; decide whether to import the constant (recommended) or pass 14.
- Deletion order: do EXIT-02 first (position_exits predicate replacement is the one behavioural trap).
- Preserved history (D-04) goes to the Claude auto-memory folder, which is outside git (repo `memory/` is gitignored).

## Metadata

**Analog search scope:** `index_ai/`, `crypto/`, `commodities/`, `dashboard/src/{hooks,components}`, `tests/`
**Pattern extraction date:** 2026-10-03
