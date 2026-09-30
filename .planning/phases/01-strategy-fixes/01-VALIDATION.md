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

Task IDs are assigned by the planner (not yet run when this file was written) — rows below are keyed by requirement until plans exist.

| Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|-----------|-------------------|-------------|--------|
| STRAT-01 | Tightened gate still returns correct `StrategySignal` shape for known fixtures | unit | `pytest tests/test_buy_strategy.py tests/test_breakout.py tests/test_candlestick_patterns.py -x` | ✅ (3 files exist, 11 tests total) | ⬜ pending |
| STRAT-01 | NIFTY buy-lane live win rate reported correctly from scorecard after tuning | manual / dashboard read | n/a — reads `strategy_performance.strategy_scorecard()` filtered to `instrument=="NIFTY"`, `strategy.startswith("candlestick_buy")` | manual-only: real trading days must elapse | ⬜ pending |
| STRAT-02 | A parameter change only ships through watching→observing→ready | unit (existing) | `pytest tests/test_strategy_learning.py -x` | ✅ | ⬜ pending |
| STRAT-03 | Strategy Lab / viability verdicts correctly flagged pre/post cutover | unit (existing) + new | `pytest tests/test_strategy_lab.py -x` (existing); new candidate needs its own test | ✅ existing / ❌ Wave 0 for the new candidate | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] A test for the new `strategy_lab.py` candidate that wraps `evaluate_buy_signal` — covers STRAT-01's D-08 sanity check and STRAT-03
- [ ] No fixture currently exists asserting `viability.py`'s buy-lane `OBSERVED_GROSS_PER_TRADE` values carry a "stale/pre-cutover" flag once that re-validation lands — needed if the plan chooses to flag rather than re-run those numbers (STRAT-03)

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
