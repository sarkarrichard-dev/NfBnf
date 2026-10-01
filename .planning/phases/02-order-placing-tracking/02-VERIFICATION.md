---
phase: 02-order-placing-tracking
verified: 2026-10-01T00:00:00Z
status: passed
score: 4/4 must-haves verified
behavior_unverified: 0
overrides_applied: 0
---

# Phase 2: Order Placing & Tracking Verification Report

**Phase Goal:** The order-placement and position-tracking code survives the specific failure
modes already flagged as untested — a cancel racing a placement, a broker disconnect
mid-position, a websocket drop under load.
**Verified:** 2026-10-01
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A test proves a cancel issued while an order is still being placed produces neither a duplicate order nor an orphaned one | ✓ VERIFIED | India: `settle_pending_entry` (`index_ai/dhan_orders.py:1289`), wired into `close_open_trade` before the exit lock (`index_ai/exit.py:212`); `wait_for_inflight_entries()` barrier (`index_ai/execution_safety.py:594`) called from `_close_all_trades_sync` (`index_ai/server.py:1942`). Crypto: Close-all barrier under `_STATE_LOCK` (`crypto/lanes.py`), `settle_entry`/`outcome_unknown`/`find_order` (`crypto/executor.py:230-271`). Proven by `tests/test_order_cancel_race.py` (cancel-wins / fill-wins / unreachable / partial / Close-all-mid-placement — all pass) and `tests/test_crypto_disconnect.py` (Close-all race). Ran both files: 113/113 pass in the targeted run, full suite 769/769. |
| 2 | A test proves a position is correctly reconciled (no stale or duplicate entry) after a simulated broker disconnection | ✓ VERIFIED | India: `sync_open_live_trades` lists trades before any broker call and reads all three indexes with `strict=True` (`index_ai/dhan_orders.py:1546-1548`), aborting before any journal write on a failed read; `reconcile()` now reads positions `strict=True` and alerts via `notify.alert` per issue kind, keyed per security+kind (`index_ai/reconcile.py:135,144,174`). Crypto: unconfirmed-entry escalation + `RECONCILE_UNREADABLE` retry marker (`crypto/lanes.py`, `crypto/executor.py:320`). Proven by `tests/test_reconcile_fault_injection.py` (16 tests: disconnect/reconnect/orphan/duplicate/lock) and `tests/test_crypto_disconnect.py` — all pass. |
| 3 | The Dhan websocket reconnecting mid-scan-cycle does not silently drop tick data a stop-trigger depends on — verified under a simulated busy cycle (20+ concurrent candles) | ✓ VERIFIED | `run_feed`'s receive loop is wrapped in `try/finally` that flushes the buffer on any exit (`_flush(buffer, sec_map)` appears exactly twice — periodic + finally); a server "disconnect" packet now breaks the outer receive loop immediately instead of waiting for the 90s stall timer (`index_ai/tick_feed.py:275-293`); `on_tick` failures are counted (`FeedState.on_tick_errors`) instead of swallowed silently. Proven on real recorded Dhan feed frames by `tests/test_tick_feed.py::test_busy_scan_cycle_no_tick_loss_and_resubscribes_on_reconnect` (25 concurrent tasks) plus the drop/shutdown/disconnect-packet/handler-failure tests — all pass. |
| 4 | When the tick feed lags or drops, stop triggering visibly falls back to candle-based evaluation, and this behavior is documented, not just known by whoever wrote it | ✓ VERIFIED | `tests/test_fast_trail_loop.py::test_crossed_stop_closes_on_the_price_check_when_the_tick_feed_is_down` forces `tick_feed._state` to disconnected/stalled and proves `_check_trails` still closes a crossed stop via the 20-second REST price check (scanner.py untouched — `git diff --stat` confirms). `guides/Strategy Guide.md`'s new "Stop checks: live ticks and the 20-second fallback" section (line 182) documents the three layers, the fallback delay, and the dashboard pill. `dashboard/src/components/shell/StatusPills.tsx` adds a real Ticks pill wired to `GET /api/tick-feed` via `useQuery`, with `is_open` sourced from the existing `status.data?.market` prop (not hardcoded) — all four states (`Ticks live`/`Ticks: fallback`/`Ticks idle`/`Ticks off`) present. `npm --prefix dashboard run build` succeeds with no new raw colors. |

**Score:** 4/4 truths verified (0 present-but-behavior-unverified)

### Code Review Findings — Fix Verification (02-REVIEW.md)

The phase's own code review found 2 Critical + 4 Warning issues after the 7 plans executed.
All six were claimed fixed in commit `a5f3b7b`. Verified directly against the current diff and
with regression tests, not from the SUMMARY's claim:

| Finding | Fix Verified | Evidence |
|---|---|---|
| CR-01: manual Close/Close-all crashes (`KeyError('side')`) on a `btc_daily_straddle` position | ✓ | `_close_position_manual_locked` now returns `{"ok": False, "error": "manual close for btc_daily_straddle is not implemented yet"}` before touching `pos["side"]` (`crypto/lanes.py:1284-1289`); `close_all_positions_manual` wraps each key in `_safe_close` try/except so one failure can't abort the dict comprehension (`crypto/lanes.py:1352-1365`). New regression tests `test_close_position_manual_on_straddle_fails_cleanly_not_a_crash` and `test_close_all_skips_straddle_cleanly_and_still_closes_the_rest` in `tests/test_crypto_phase2.py` — both pass. |
| CR-02: Delta client's non-JSON 5xx response leaves `DeltaError.status=None`, defeating `outcome_unknown()` | ✓ | `crypto/delta/client.py`'s `except ValueError` branch now raises `DeltaError(..., status=resp.status_code)` (line ~258). New regression test `test_delta_5xx_with_non_json_body_is_an_unknown_outcome` in `tests/test_crypto_order_settlement.py` — passes. |
| WR-01: `ALREADY_CLOSED` folded into `BLOCKED`, misreporting a legitimate concurrent-close race as a Close-all failure | ✓ | `index_ai/exit.py:219` now does `if settled_status in {"CANCELLED", "ALREADY_CLOSED"}: return {"status": settled_status, ...}`. No dedicated new test for this exact pass-through path was added (minor gap — see below), but the fix is directly inspectable and logically sound against `server.py`'s existing `{CLOSED, ALREADY_CLOSED, CANCELLED}` success set. |
| WR-02: `wait_for_inflight_entries` could miss an instrument's very first in-flight entry (lazy lock creation) | ✓ | `wait_for_inflight_entries()` now pre-touches every configured instrument's lock via `configured_index_keys()` (`index_ai/execution_safety.py:612-615`) before taking the snapshot. `configured_index_keys()` confirmed to exist in `index_ai/instruments.py:150`. |
| WR-03: stale fixture README referencing a non-existent `dhan_journal_orders.jsonl` | ✓ | README now documents journal rows as merged into `dhan_rest.jsonl` with `"source": "journal"` (`tests/fixtures/broker_traffic/README.md:14`). |
| WR-04: real public IP committed in `delta_rest.jsonl` | ✓ | `client_ip`/`ip` added to `REDACT_KEYS` (`scripts/capture_broker_traffic.py:62-63`); the committed fixture now reads `"client_ip": "REDACTED"` in every row. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `index_ai/dhan_orders.py::settle_pending_entry` | Cancel-vs-fill race resolution | ✓ VERIFIED | Defined line 1289; wired into `exit.py` before the exit lock |
| `index_ai/execution_safety.py::wait_for_inflight_entries` | Close-all barrier | ✓ VERIFIED | Defined line 594; called from `server.py:1942`; WR-02 fix present |
| `index_ai/reconcile.py` strict read + alerts | Disconnect-safe reconcile | ✓ VERIFIED | `strict=True` at line 174; `notify.alert` per issue kind |
| `index_ai/dhan_orders.py::sync_open_live_trades` | Disconnect-safe sync | ✓ VERIFIED | `strict=True` builder calls at lines 1546-1548 |
| `crypto/executor.py::outcome_unknown/find_order/settle_entry` | Crypto lost-reply settlement | ✓ VERIFIED | Lines 230, 241, 256; CR-02 fix confirmed in `crypto/delta/client.py` |
| `crypto/lanes.py` Close-all barrier, unclear-entry escalation | Crypto race + outage handling | ✓ VERIFIED | `UNCLEAR_ALERT_SECONDS = 180` line 50; `RECONCILE_UNREADABLE` retry marker |
| `index_ai/tick_feed.py` flush-in-finally, disconnect handling, `on_tick_errors` | Websocket drop survival | ✓ VERIFIED | `_flush` called twice (periodic + finally); disconnect packet breaks outer loop; `on_tick_errors` counter present and exposed |
| `tests/test_fast_trail_loop.py` fallback proof | ORD-04 proof | ✓ VERIFIED | New tests force feed down, `scanner.py` untouched |
| `guides/Strategy Guide.md` "Stop checks" section | ORD-04 documentation | ✓ VERIFIED | Line 182, three layers + reconnect + pill meanings documented |
| `dashboard/src/components/shell/StatusPills.tsx` Ticks pill | ORD-04 dashboard indicator | ✓ VERIFIED | Wired to real `/api/tick-feed`, real `market.is_open`, `App.tsx` untouched, build succeeds |

### Key Link Verification

| From | To | Via | Status |
|---|---|---|---|
| `index_ai/exit.py close_open_trade` | `index_ai/dhan_orders.py settle_pending_entry` | called before `_exit_lock`, never under it | ✓ WIRED |
| `index_ai/server.py _close_all_trades_sync` | `index_ai/execution_safety.py wait_for_inflight_entries` | barrier before `open_trades()` | ✓ WIRED |
| `crypto/lanes.py _apply_entry` | `crypto/executor.py settle_entry` / `outcome_unknown` | except branch on lost POST reply | ✓ WIRED |
| `index_ai/dhan_orders.py sync_open_live_trades` | `build_order_book_index/build_trade_fill_index/build_position_index(strict=True)` | abort-before-write pattern | ✓ WIRED |
| `index_ai/reconcile.py reconcile` | `index_ai/notify.py alert` | lazy import, try/except, per-security+kind key | ✓ WIRED |
| `index_ai/tick_feed.py run_feed` | `index_ai/scanner.py on_index_tick` | per-packet call, errors now counted not swallowed | ✓ WIRED |
| `dashboard/src/components/shell/StatusPills.tsx` | `index_ai/server.py GET /api/tick-feed` | `useQuery` via `api()`, 20s refetch | ✓ WIRED |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| Ticks pill | `tickFeed` | `GET /api/tick-feed` → `tick_feed.status()` (real `FeedState`) | Yes | ✓ FLOWING |
| Ticks pill market-open branch | `marketOpen` | `status.data?.market.is_open`, computed server-side from real session clock | Yes | ✓ FLOWING |

### Behavioral Spot-Checks / Test Execution

| Behavior | Command | Result | Status |
|---|---|---|---|
| Targeted phase tests (cancel race, reconcile fault injection, tick feed, fast trail, crypto phase2/settlement/disconnect) | `python -m pytest tests/test_order_cancel_race.py tests/test_reconcile_fault_injection.py tests/test_tick_feed.py tests/test_fast_trail_loop.py tests/test_crypto_phase2.py tests/test_crypto_order_settlement.py tests/test_crypto_disconnect.py -q` | 113 passed in 50.24s | ✓ PASS |
| CR-01 regression tests by name | `pytest tests/test_crypto_phase2.py -k straddle -v` | 3 passed | ✓ PASS |
| CR-02 regression test by name | `pytest tests/test_crypto_order_settlement.py -k "non_json or status" -v` | 1 passed | ✓ PASS |
| Full suite (independent run, not trusting SUMMARY's count) | `python -m pytest -q` | **769 passed in 279.54s** — exactly 766 (last confirmed pre-review-fix count) + 3 new regression tests (CR-01 x2, CR-02 x1) | ✓ PASS |
| Lint, no new errors vs baseline | `ruff check index_ai/ crypto/ scripts/` | 8 errors — matches CLAUDE.md's documented ~8 pre-existing cosmetic baseline | ✓ PASS |
| Dashboard build | `npm --prefix dashboard run build` | built in 1.05s, no TS errors | ✓ PASS |
| Debt markers in phase-touched files | grep `TBD\|FIXME\|XXX` across `crypto/lanes.py`, `crypto/delta/client.py`, `index_ai/dhan_orders.py`, `index_ai/exit.py`, `index_ai/execution_safety.py`, `index_ai/server.py`, `index_ai/reconcile.py`, `index_ai/tick_feed.py`, `StatusPills.tsx` | none found | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| ORD-01 | 02-01, 02-02, 02-03, 02-04 | Cancel-racing-placement produces neither duplicate nor orphaned order | ✓ SATISFIED | `settle_pending_entry`, `wait_for_inflight_entries`, crypto `settle_entry`/Close-all barrier — all proven by passing tests |
| ORD-02 | 02-01, 02-02, 02-03, 02-05 | Position correctly reconciled after broker disconnection | ✓ SATISFIED | `strict=True` reads abort-before-write; `reconcile()` alerts; `tests/test_reconcile_fault_injection.py`, `tests/test_crypto_disconnect.py` pass |
| ORD-03 | 02-06 | Dhan websocket reconnect under busy cycle doesn't silently drop ticks | ✓ SATISFIED | flush-in-finally, immediate disconnect-packet reconnect, `on_tick_errors` counter; busy-cycle test (25 concurrent tasks) passes on real recorded frames |
| ORD-04 | 02-07 | Tick fallback to candle-based evaluation, documented | ✓ SATISFIED | Fallback proven unchanged (scanner.py untouched), documented in Strategy Guide, shown via real dashboard pill |

All four requirement IDs declared in PLAN frontmatter (ORD-01..04) match REQUIREMENTS.md's
Phase 2 mapping exactly — no orphaned requirements found.

### Anti-Patterns Found

None found in phase-touched files. No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers,
no stub returns, no hardcoded empty data flowing to rendered output. The one deliberate
documented simplification (`ponytail:` comment in `index_ai/tick_feed.py` acknowledging a
second-cancellation-during-flush ceiling) is explicitly named with its ceiling, matches the
project's own convention, and is accurately reflected in the Strategy Guide wording (IN-02 fix
confirmed: guide no longer overstates the guarantee).

### Human Verification Required

None. All four success criteria are backed by passing automated tests that exercise the actual
failure mode (fault-injected network errors, forced-down feed state, held locks across threads,
recorded real broker traffic), not just symbol presence. The one UI acceptance check in
02-07-PLAN.md's Task 3 (`<human-check>` — hover the dashboard pill, confirm tooltip text) is a
cosmetic/visual confirmation of a mechanism already proven by the build and the status-mapping
logic read directly from the component source; not required to confirm the phase goal (order
survival under cancel/disconnect/drop) is achieved.

### Minor Observations (non-blocking)

- WR-01's fix (exit.py's `ALREADY_CLOSED` pass-through) has no test asserting the exact scenario
  it fixes (a concurrent close winning the race while another close is in flight returns
  `ALREADY_CLOSED` rather than `BLOCKED`). The fix is small, directly readable, and consistent
  with `server.py`'s existing success-set contract, so this is a documentation/coverage gap, not
  a functional doubt — flagged for awareness, not scored as a gap.

### Gaps Summary

None. All four ROADMAP success criteria verified with passing, independently-run automated
tests against real recorded broker traffic and real fault injection. Both critical and all four
warning findings from the phase's own code review are confirmed fixed in the current codebase
(not just claimed in a SUMMARY), each critical with a new named regression test. The full test
suite (769 tests) and the dashboard build both pass independently. Phase goal achieved.

---

_Verified: 2026-10-01_
_Verifier: Claude (gsd-verifier)_
