---
name: status-check
description: Check live health across every Algo BNF section — India options, crypto, commodities, and the three separate ML models — plus a real error-log scan, in one pass instead of five separate manual checks
---

# Status check

Richard periodically asks "check the India options / crypto / commodities /
ML strategies" as separate requests. Each one is the same handful of curl
calls against the running server (`http://127.0.0.1:8000`) plus a log scan —
this runs all of them in one pass and reports plainly, section by section.

If the server isn't reachable (`curl .../api/health` fails), say so
immediately and stop — nothing else below works without it.

## Steps

1. **Overall health.** `GET /api/health` (confirms the process is up) and
   `GET /api/status` for `trading_mode`, `auto.running`, `auto.last_error`,
   `auto.cycles`, `auto.started_at_ist`, and `auto.market.message` (is it a
   trading day right now, or closed for the weekend/a holiday). A non-null
   `last_error` here is the single most important thing to surface first.

2. **India options.** From the same `/api/status` payload: `auto.open_trades`
   and `auto.kill_switch` (tripped or not, today's realized P&L). Then
   `GET /api/strategy-learning`'s `india` array for the per-(strategy,
   instrument) scorecard — trades, win rate, net, and `state`/`frozen`. Note
   which pairs have crossed 15 trades (eligible for pattern-flagging) or 40+
   (eligible for a tuning suggestion) per the confidence-ladder rule in
   `memory/strategy-analysis-and-simplification-directive.md` — most days
   nothing has, and that's fine to say plainly rather than force a finding.

3. **Crypto.** `GET /api/crypto/status` for `trading_mode`, `live_armed`,
   `symbols` (active) vs `available_symbols`, `lanes` (which strategies are
   enabled), `kill_switch`, and the embedded `ml` field (`gate_armed`,
   `oos_delta_usd` — armed only if it's genuinely proven better than trading
   everything). `GET /api/crypto/positions` for open paper positions with
   live `unrealized_usd`. Then the `crypto` array from the same
   `/api/strategy-learning` call in step 2 for the scorecard.

4. **Commodities.** `GET /api/commodities/status` — `enabled` (this lane
   defaults to paused; confirm it matches what Richard last asked for, don't
   assume it should be on), `open_positions`, `today`, `all_time`.

5. **The India live-order ML model** (the one that actually gates real/paper
   option entries — separate from the paper-futures brain model in step 6).
   `GET /api/learning` for `learned.ml` (version, holdout accuracy, training
   sample count — flag if it's under ~40, since that's thin), and
   `learned.oi_insights.recommendations` (the system's own plain-language
   findings, e.g. "OI-aligned setups only win 33%" — surface these verbatim,
   they're often the most concrete thing to report).

6. **The paper-futures brain model.** `GET /api/brain/status` —
   `gate_armed`, `oos_delta_rupees` vs `oos_static_rupees` (only meaningful
   if `gate_armed` is true; report plainly if it isn't armed and why that's
   correct behavior, not a bug — an unarmed gate means the walk-forward
   check didn't prove it helps).

7. **Real error scan.** Tail the last ~300-400 lines of `memory/server.log`
   and grep for `error|exception`, excluding known-benign noise that is NOT
   worth reporting: `getaddrinfo failed` (transient DNS blips, self-recover),
   `ConnectionResetError`, `_call_connection_lost`, `Proactor` (Windows
   asyncio artifacts from any client disconnecting abruptly, including your
   own aborted test requests). Anything else that survives that filter is a
   real finding — quote it.

## Output

One plain-language summary, section by section — not a wall of raw JSON.
For each of India / crypto / commodities: is it running as expected
(enabled/disabled matching what was last asked for), any open positions and
their live P&L, and 2-3 scorecard standouts (best and worst performer) if
any pairs have enough trades to say anything at all. For ML: each of the
three models' armed/not-armed state and the one or two most concrete
findings each has surfaced on its own (don't invent conclusions from
still-thin data — say "too early to tell" when that's the honest answer).
End with a short "needs attention" list — empty is a fine and common answer,
state it plainly rather than manufacturing a concern.
