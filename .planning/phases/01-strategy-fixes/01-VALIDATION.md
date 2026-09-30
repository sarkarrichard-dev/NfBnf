---
phase: "01"
slug: "strategy-fixes"
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-30"
---

# Phase 01 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (`pyproject.toml:47-48`, `testpaths = ["tests"]`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `python -m pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_learning.py tests/test_strategy_lab.py -q` |
| **Full suite command** | `python -m pytest -q` |
| **Estimated runtime** | ~450-480 seconds (660 tests, ~7-8 min per CLAUDE.md) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_lab.py tests/test_strategy_learning.py -q`
- **After every plan wave:** Run `python -m pytest -q` (full suite)
- **Before `/gsd-verify-work`:** Full suite must be green; `ruff check index_ai/` compared against `git stash` baseline (~8 pre-existing cosmetic errors expected, per CLAUDE.md — don't chase those to zero)
- **Max feedback latency:** ~480 seconds (full suite)

---

## Per-Task Verification Map

Task IDs assigned by the planner (2026-09-30).

| Task | Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|------|-------------|----------|-----------|-------------------|-------------|--------|
| 01-01 T1 (tracer) | STRAT-01, STRAT-02 | CPR-direction gate inert when off, blocks counter-trend when on; live buy lane replays on real chain | unit + real-data CLI | `python -m pytest tests/test_buy_strategy.py tests/test_strategy_lab.py -q` then `python -m index_ai.strategy_lab NIFTY` | ✅ files exist; new tests added in-task | ⬜ pending |
| 01-01 T2 | STRAT-01 | Replay has no look-ahead, degrades on missing data, maps directions, never runs from the dashboard endpoint; gate symmetric | unit | `python -m pytest tests/test_buy_strategy.py tests/test_strategy_lab.py tests/test_strategy_learning.py -q` | ✅ | ⬜ pending |
| 01-02 T1 | STRAT-03 | Buy-lane viability reads UNMEASURED, sell rows unchanged | unit | `python -m pytest tests/test_options_cpr.py -q -k viability` | ✅ | ⬜ pending |
| 01-02 T2 | STRAT-01, STRAT-02 | `strategy_scorecard(since=...)` counts only trades after the cut-off; default unchanged | unit | `python -m pytest tests/test_strategy_performance.py tests/test_strategy_learning.py -q` | ✅ | ⬜ pending |
| 01-03 T1 | STRAT-01, STRAT-03 | OI-wall room gate inert when off, blocks buys into a near wall when on; full tuned bundle | unit | `python -m pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py tests/test_strategy_lab.py -q` | ✅ | ⬜ pending |
| 01-03 T2 | STRAT-01 | Dashboard panel shows the two buy switches | build | `npm --prefix dashboard run build` and `npx tsc --noEmit` in dashboard/ | ✅ | ⬜ pending |
| 01-03 T3 | STRAT-01 | Three-index real-chain sanity run recorded with plain read | real-data CLI | `python -m index_ai.strategy_lab` | n/a | ⬜ pending |
| 01-04 T3 | STRAT-01, STRAT-02 | Baseline and switch-on recorded; measuring command works | CLI | `python -c "...strategy_scorecard(since=...)..."` (full text in 01-04-PLAN.md) | n/a | ⬜ pending |
| — | STRAT-02 | Ladder thresholds untouched | unit (existing) | `python -m pytest tests/test_strategy_learning.py -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] A test for the new `strategy_lab.py` candidate that wraps `evaluate_buy_signal` — covers STRAT-01's D-08 sanity check and STRAT-03 (created inside 01-01 T1/T2, tests first in T2)
- [ ] A test asserting the buy-lane viability rows are gone (UNMEASURED, gross None) — created inside 01-02 T1 (the plan removes the Black-Scholes-proxy buy rows rather than keeping them with a flag)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| NIFTY buy-lane real win rate + net rupees over 40+ paper trades | STRAT-01 | Requires real trading days to elapse on the paper lane (D-06/D-07, CONTEXT.md) — no unit test can fabricate live-journal trades | Read `strategy_performance.strategy_scorecard()` (or its dashboard tab) filtered to `instrument=="NIFTY"`, `strategy.startswith("candlestick_buy")`; confirm ≥40 trades, ≥65% win rate, and net-positive rupees before calling the lever "fixed" |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 480s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
