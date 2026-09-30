---
status: testing
phase: 01-strategy-fixes
source: [01-VERIFICATION.md]
started: 2026-09-30T12:52:38Z
updated: 2026-09-30T13:05:00Z
---

## Current Test

number: 1
name: Whether to revisit the buy-lane tightening trial
expected: |
  A recorded decision (this phase already has one: Hold, 2026-09-30). If revisited
  and switched on, `strategy_scorecard(since=FLIP)` per the recipe in
  `01-04-SUMMARY.md` becomes the path to close STRAT-01.
awaiting: none — Richard answered directly in chat this session, before this UAT
  file was created (see result below)

## Tests

### 1. Whether to revisit the buy-lane tightening trial
expected: A recorded decision (this phase already has one: Hold, 2026-09-30). If
  revisited and switched on, `strategy_scorecard(since=FLIP)` per the recipe in
  `01-04-SUMMARY.md` becomes the path to close STRAT-01.
result: PASS — Richard confirmed **Hold** (2026-09-30, in chat, after being shown
  01-03's real-chain sanity-check numbers: NIFTY and BANKNIFTY both worse on win
  rate and net rupees with the tuned bundle; only SENSEX improved, and the two new
  gates weren't isolated from each other in that run). Neither `BUY_BLOCK_CONTRA_CPR`
  nor `BUY_BLOCK_INTO_OI_WALL` is switched on. STRAT-01 (buy lane win rate moved to
  target) stays open as future work — a later phase/plan is needed if Richard
  chooses to revisit, starting with 01-03's own suggestion to test the
  CPR-direction gate alone, isolated from the OI-wall gate.

## Summary

total: 1
passed: 1
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

None — the only open item (STRAT-01, live win rate moved to ≥65%) is not a gap in
this phase's delivered work. It is out of scope for Phase 1 to close by itself: the
phase built the tooling and gates, ran the human decision checkpoint exactly as
designed, and the decision was Hold. Closing STRAT-01 requires either a future
switch-on trial (Richard's call) or a different approach — tracked as follow-up
work, not a defect in what this phase shipped.
