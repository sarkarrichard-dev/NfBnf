---
name: new-strategy-backtest
description: Scaffold a backtest for a new strategy candidate (from a video, a TradingView indicator, or a spec) following Algo BNF's established rigorous methodology — real charges, every instrument, walk-forward — before any code is wired into a live or paper lane
---

# New strategy backtest scaffold

Richard periodically shares a strategy from a reel, a YouTube video, or a
paid TradingView indicator and asks for it to be tested. Per
`strategy-findings.md`, **every one tested so far has come back
net-negative** — the standing expectation is "probably no edge" until proven
otherwise, and the project has been burned before by a backtest that looked
good but used the wrong assumption (a hand-picked spread, a naive charge
model, a data-layer timestamp bug). This skill exists so every new candidate
gets the same rigor, not a shortcut version.

## Before writing any backtest code

1. **Get the real signal, not a guess.** If Richard references a paid
   indicator he has a licence for, study it live in his browser (the
   `claude-in-chrome` MCP) rather than reverse-engineering from public
   release notes — a guessed AK Roxx Pro spec once made a backtest
   meaningless because the ported signal was simply wrong. If it's a video,
   check whether transcription is warranted (`faster-whisper` works offline)
   or whether the spec is clear enough from what Richard describes.
2. **Confirm which universe this strategy targets** — Indian indices
   (NIFTY/BANKNIFTY/SENSEX), MCX commodities, or crypto (Delta perps). The
   charge model and instrument loop differ by venue; do not backtest on only
   one instrument if the strategy is meant to apply to a whole venue (see
   `project-algo-bnf-vision.md` — "when a strategy only works on one index,
   the answer is a per-instrument structure, not dropping the other
   indices").

## Scaffold

Create `scripts/backtest_<strategy_name>.py` following the existing pattern
in `scripts/backtest_stock_futures.py` / `scripts/backtest_cloud_exit_ab.py`:

- Import the **real** charge model for the venue — `index_ai.strategies.charges`
  (or `commodities.charges` / `crypto.charges`) — never a hardcoded or
  estimated cost constant. Every prior "looked profitable" backtest that
  didn't survive was because of an unmeasured assumption (bid-ask spread,
  charge formula) doing the deciding.
- Loop **every relevant instrument** for the venue, not a single symbol —
  print a per-instrument table plus a portfolio total, matching
  `STOCK_RESULTS.md`'s format.
- Use real historical candles, not a synthetic/proxy price series, unless the
  venue genuinely has no historical option chain (in which case, flag the
  result as "relative comparisons only, not absolute P&L" exactly like the
  existing options Black-Scholes-proxy backtests do).
- Report: trades, win rate, gross P&L, net P&L (after the real charge
  model), profit factor, and a half-spread/cost sensitivity sweep if the
  result is close to breakeven.
- Sanity-check bars-per-session after loading data (375/75/25 for
  1m/5m/15m on an Indian trading day) — a prior bug silently replayed only
  the last ~40 minutes of each day due to a timezone mismatch and it looked
  plausible until checked.

## After the backtest runs

- State the verdict plainly, in rupees/dollars, not in abstract terms
  ("−₹2,100 over 45 days", never "the edge was eaten by friction" — Richard
  has explicitly rejected the word "friction"; describe the actual broker
  charges in the currency of the venue).
- If net-negative (the likely outcome per prior history), say so directly —
  don't soften it — and record it in the relevant `RESULTS.md` /
  `strategy-findings.md`-style file so it isn't re-tested later.
- Only if genuinely positive across the full instrument set and net of real
  costs: propose wiring it in as an **additive** new lane (a new strategy
  option alongside existing ones, tracked separately in the per-(strategy,
  instrument) scorecard), defaulting to paper, never suggesting it replace
  an existing strategy outright.
