---
status: complete
phase: 04-dashboard-ui-ux
source: [04-VERIFICATION.md]
started: 2026-10-03
updated: 2026-10-03
---

## Current Test

number: 1
name: Data Health panel and live/paper list (after a server restart; do NOT arm anything)
expected: |
  Index Options page: a "Data health" panel at the top with four tiles (Live prices, Option prices,
  Buy/sell price gap, Dhan live feed) all grey on the closed market, none red or amber.
  Crypto tab: under the Paper/Live switch a list of coin+strategy pairs, each LIVE or PAPER with a plain
  reason; nothing LIVE while unarmed; no retired coins; the old "Go-live readiness" block is gone.
  Narrow (phone-width) window: tiles in two columns, nothing overflows.
  Also confirm: Data Health at the top of the Index Options page (not beside the header pills) is fine.
awaiting: user response (needs a server restart first)

## Tests

### 1. Data Health panel, live/paper list, phone width, placement
expected: see above
result: passed (Richard, 2026-10-03: "Phase 4 looks good")

## Summary

total: 1
passed: 1
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps
