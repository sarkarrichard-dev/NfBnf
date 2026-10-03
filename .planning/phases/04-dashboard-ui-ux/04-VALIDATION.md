---
phase: "04"
slug: "dashboard-ui-ux"
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-10-03"
---

# Phase 04 - Validation Strategy

> Per-phase validation contract. Derived from 04-RESEARCH.md "Validation Architecture"; plans' verify steps must use these commands.

## Validation Architecture (from research)

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


## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Wave 0 covers all missing references
- [ ] Feedback latency < 480s

**Approval:** pending
