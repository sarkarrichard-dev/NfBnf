---
phase: "02"
slug: "order-placing-tracking"
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-30"
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest `>=8` (`pyproject.toml:29`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `norecursedirs = ["reference"]` |
| **Quick run command** | `python -m pytest tests/test_reconcile.py tests/test_dhan_orders.py tests/test_tick_feed.py tests/test_tick_driven_stops.py tests/test_crypto_phase*.py -x` (adjust to the actual new/touched test files per task) |
| **Full suite command** | `python -m pytest -q` |
| **Estimated runtime** | ~450-480 seconds (660+ tests, ~7-8 min per CLAUDE.md) |

---

## Sampling Rate

- **After every task commit:** Run the relevant file(s) from the Phase Requirements → Test Map below, `-x`.
- **After every plan wave:** Run `python -m pytest -q` (full suite).
- **Before `/gsd-verify-work`:** Full suite must be green; `ruff check index_ai/` compared against `git stash` baseline (~8 pre-existing cosmetic errors expected, per CLAUDE.md).
- **Max feedback latency:** ~480 seconds (full suite).

---

## Per-Task Verification Map

Task IDs are assigned by the planner (not yet run when this file was written) — rows below are keyed by requirement until plans exist.

| Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|-----------|-------------------|-------------|--------|
| ORD-01 | Cancel issued mid-placement produces neither a duplicate nor an orphaned order | unit | `pytest tests/test_order_cancel_race.py -x` (new file) | ❌ Wave 0 — no cancel code exists yet | ⬜ pending |
| ORD-02 | Position reconciled correctly after simulated broker disconnection | unit | `pytest tests/test_scan_health_reconcile.py -x` (extend) or new `tests/test_reconcile_fault_injection.py` | Partial — `diff_positions`/`expected_positions` already unit-tested; disconnect-simulation cases are ❌ Wave 0 | ⬜ pending |
| ORD-03 | Websocket reconnect mid-scan-cycle (20+ concurrent candles) does not silently drop tick data | unit/integration | `pytest tests/test_tick_feed.py -x` (extend with async `run_feed` fault-injection) | Partial — only `parse_packet`/`status`/`enabled` tested today; `run_feed`'s reconnect/flush path is ❌ Wave 0 | ⬜ pending |
| ORD-04 | Tick-driven stop degrades to candle fallback visibly, documented | unit + doc | `pytest tests/test_tick_driven_stops.py tests/test_fast_trail_loop.py -x` (extend) | Partial — tick-crossing mechanics tested; an explicit "tick feed stalled, 20s loop still closes" test is ❌ Wave 0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] A reusable fake-client test helper for `DhanClient`/`DeltaClient` fault injection (`tests/_fake_brokers.py`) — no shared fixture exists project-wide today; every test file hand-rolls its own stub class, and all four requirements need one.
- [ ] `tests/fixtures/broker_traffic/` (or equivalent) — the D-03 captured real-traffic payloads the fault-injection tests replay. Must exist before ORD-01/02/03 tests can be written. **Secrets must be redacted before capture** (`access-token` for Dhan, `api-key`/`signature` for Delta) — a real security finding from research, not optional.
- [ ] `tests/test_order_cancel_race.py` — new, depends on the cancel mechanism itself being designed first (no `cancel_order` exists for either broker today).
- [ ] An async fault-injection extension to `tests/test_tick_feed.py` that drives `run_feed` against a fake `websockets.connect` (monkeypatched, matching this repo's existing no-mocking-library style) raising a non-`TimeoutError` exception mid-stream, to pin the real data-loss bug research found at `index_ai/tick_feed.py:216-263`.

---

## Manual-Only Verifications

*None — every behavior in this phase has an automated, repeatable test path (this is plumbing correctness work, not a live-measurement outcome like Phase 1's strategy tuning).*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 480s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
