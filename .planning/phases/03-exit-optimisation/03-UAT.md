---
status: complete
phase: 03-exit-optimisation
source: [03-VERIFICATION.md]
started: 2026-10-03
updated: 2026-10-03
---

## Current Test

number: 1
name: Stop check panel on the Strategy P&L tab
expected: |
  After the server is restarted: open the Strategy P&L tab. A "Stop check" panel lists 8 rows
  (NIFTY, BANKNIFTY, SENSEX buy and sell, crypto, MCX commodities), each saying "Not enough data yet ..."
  with its real numbers. Press "Re-check now" once: a spinner, a "Stop check done" message, and the
  "last run" time updates. No "changed" flag, and no stop value changes anywhere.
awaiting: user response (needs a server restart first)

## Tests

### 1. Stop check panel on the Strategy P&L tab
expected: see above
result: passed (Richard, 2026-10-03, after server restart)

## Summary

total: 1
passed: 1
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps
