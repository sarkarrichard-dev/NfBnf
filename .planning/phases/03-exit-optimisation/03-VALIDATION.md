---
phase: "03"
slug: "exit-optimisation"
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-10-03"
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.3 (`[tool.pytest.ini_options] testpaths = ["tests"]` in `pyproject.toml`) |
| **Config file** | `pyproject.toml`; shared isolation in `tests/conftest.py` (autouse: Telegram vars cleared, `.env` and `index_ai.learning.DB_PATH` / `index_ai.market_log.DB_PATH` redirected) |
| **Quick run command** | `python -m pytest tests/test_exit_recheck.py tests/test_position_exits.py tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_credit_spread.py tests/test_profit_trail.py tests/test_fast_trail_loop.py tests/test_tick_driven_stops.py tests/test_exit_credit.py tests/test_strategy_learning.py tests/test_strategy_performance.py tests/test_day_review.py -q` (~10 s without the new file) |
| **Full suite command** | `python -m pytest -q` |
| **Estimated runtime** | ~210-480 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** the quick run command above (deletion-only tasks: the ~10 s legacy set; tasks touching `index_ai/exit_recheck.py`: add `tests/test_exit_recheck.py`).
- **After every plan wave:** quick command + `ruff check index_ai/` compared against the pre-existing cosmetic errors via `git stash`, then the full suite.
- **Before `/gsd-verify-work`:** full suite green; `npm --prefix dashboard run build` succeeds; `trading-safety-reviewer`, `strategy-tuning-reviewer`, `test-isolation-reviewer`, `ui-consistency-reviewer` run; manual look at the Strategy P&L tab on a rebuilt bundle.
- **Max feedback latency:** ~480 seconds (full suite).

---

## Per-Task Verification Map

Task IDs are assigned by the planner (not yet run when this file was written) — rows are keyed by requirement until plans exist.

| Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|-----------|-------------------|-------------|--------|
| EXIT-02 | `index_ai.premium_trail` no longer importable; no `premium_trail` identifier left in `index_ai/` or `tests/` (excluding `.claude/worktrees`, `graphify-out`) | unit / source-scan | `python -m pytest tests/test_exit_recheck.py::test_premium_trail_is_gone -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-02 | Sell-lane credit spread on NIFTY/BANKNIFTY/SENSEX still ignores signal-flip / regime exit; FINNIFTY still closes on it (behaviour of `position_exits` unchanged after the predicate swap) | unit | `python -m pytest tests/test_position_exits.py -q` | ✅ (rename one test) | ⬜ pending |
| EXIT-02 | Buy trail unchanged (25/55 pt from the first point, no option-price early close); `evaluate_open_trade` works with the premium branch removed and the rupee `profit_trail` fallback still reachable | unit | `python -m pytest tests/test_buy_scalp_trail.py tests/test_trailing.py tests/test_profit_trail.py tests/test_credit_spread.py -q` | ✅ | ⬜ pending |
| EXIT-01 | Exit classifier maps every verbatim real India / crypto / commodity exit reason to the right class; unknown → "other" | unit | `python -m pytest tests/test_exit_recheck.py::test_exit_classifier -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-01 | Segment builder: India lane × instrument; "under today's distance" filter by the trade's own recorded `it_points` / `trail_distance_points`; crypto excludes `ak_roxx_pro` and `btc_daily_straddle`; data-epoch cut-off | unit | `python -m pytest tests/test_exit_recheck.py::test_segments -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-01 | Verdict ladder: below the ladder bar → "not enough data yet" with numbers still returned; ready + frozen → no suggestion; ready + not frozen + replay better → plain-language suggestion, never applied | unit | `python -m pytest tests/test_exit_recheck.py::test_verdict_ladder -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-01 | India replay at the CURRENT distance reproduces a synthetic real-looking trade's exit; a missing chain quote → "could not price" | unit (tmp `market_log` like `tests/test_strategy_lab.py`) | `python -m pytest tests/test_exit_recheck.py::test_india_replay -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-03 | Drift: more than 15 pp on hit-rate or win-rate AND n ≥ 40 → DRIFT; exactly 15 pp or n < 40 → OK; baseline persists across a simulated restart; a changed stop distance re-baselines | unit | `python -m pytest tests/test_exit_recheck.py::test_drift -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-03 | Alert fires once on OK→DRIFT, not on the next run while still drifting, fires again after recovery plus a new drift; key shape `exit-drift:<segment>:<baseline>` | unit (monkeypatch `index_ai.notify.alert`) | `python -m pytest tests/test_exit_recheck.py::test_drift_alert_once -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-01/03 | `daily_ops.run_eod()` includes an `exit_recheck` entry; a failure inside it never breaks the rest of the EOD report | unit | `python -m pytest tests/test_exit_recheck.py::test_run_eod_hook -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-01/03 | `GET /api/exit-recheck` and `POST /api/exit-recheck/run` return the stored / fresh result; POST runs off the event loop and cannot change any stop value | API (`TestClient(app)`, pattern `tests/test_admin_auth.py`) | `python -m pytest tests/test_exit_recheck.py::test_endpoints -q` | ❌ Wave 0 | ⬜ pending |
| EXIT-03 | Dashboard flag + "Re-check now" button build and type-check | build | `npm --prefix dashboard run build` ; `npx tsc --noEmit` (run in `dashboard/`) | n/a (no JS test runner exists) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_exit_recheck.py` — every ❌ row above (use synthetic fixtures; no segment can drift on real data today — every India segment has 3-10 trades under today's stop distance)
- [ ] A feasibility spike for the India replay: reproduce, at the CURRENT distance, the real exits of the trades since 2026-09-25 before any suggestion UI is built; open `memory/market_log.sqlite` read-only (`mode=ro` URI); never write the real databases
- [ ] Test fixtures: tmp `crypto.journal.JOURNAL_PATH`, tmp `commodities.lanes.JOURNAL_PATH`, a `data_epoch` stub (conftest redirects `index_ai.learning.DB_PATH` and `index_ai.market_log.DB_PATH`, but NOT the crypto/commodity journals or `data_epoch`)
- Framework install: none needed

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| The re-check panel and "Re-check now" button look right and the button works on the Strategy P&L tab | EXIT-01/03 | There is no JS test runner, and a running server needs `DASHBOARD_PASSWORD` that only Richard can read from `.env` | Rebuild the dashboard, open the Strategy P&L tab, press the button once, confirm the panel updates and no stop value changed |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 480s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
