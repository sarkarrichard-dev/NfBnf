# Algo BNF — working notes for Claude

FastAPI backend (`index_ai/`, ~14k LOC) + React/Vite dashboard (`dashboard/`).
Indian index options/futures on the Dhan broker (NSE/BSE F&O). NIFTY, BANKNIFTY,
SENSEX — all three, never just one.

## Environment

- **Windows / PowerShell.** Never hand the user `KEY=value` bash syntax.
- **`.env` is permission-blocked** in these sessions — you cannot read or write it.
  To change a setting: tell the user the exact line, or use the running app.
  `POST /api/settings/features` (backed by `config.update_env_values`, which
  preserves every existing key) toggles non-financial flags; there's a "Feature
  toggles" panel in the dashboard's Setup tab. Anything that can move money is
  excluded from that path on purpose. `set_feature_flag()` is the same function
  called server-side — fine to call directly for a flag in `TOGGLEABLE_FLAGS`.
- The server must be restarted to pick up an `.env` change.
- **`DASHBOARD_PASSWORD` gates the whole API** (HTTP Basic, any username) once
  set — added 2026-09-28 after finding it was configured but never enforced
  (read before `.env` was loaded, always empty). In cloud mode
  (`PUBLIC_DEPLOY=true`) the server now refuses to start without a 12+ char
  password or a `WORKER_TOKEN`; `/api/health` stays open for probes; 10 wrong
  attempts locks an address out 15 min. This means Claude generally can't hit
  the local API directly anymore without the password — ask the user to read
  it from `.env` rather than trying to bypass it.
- Run the server: `python -m uvicorn index_ai.server:app --port 8000`. Dashboard
  is served from `dashboard/dist/` — **rebuild it (`npm --prefix dashboard run
  build`) after any `dashboard/src` change** or you'll debug a stale bundle.
- **Never `claude mcp add --scope project` a server that takes a credential** —
  project scope writes straight into the committed `.mcp.json`. Use local scope
  (the default, omit `--scope`) for anything with a token. `playwright` is the
  one project-scoped (shared, no secret) MCP server; a GitHub server, if added,
  must be named something other than `github` — a global `github` plugin
  already occupies that name and silently shadows a same-named local one.

## Checks before committing

- `python -m pytest -q` — 660 tests, ~7-8 min. Keep it green.
- `ruff check index_ai/` — **~8 pre-existing cosmetic errors** (unused locals,
  ambiguous `l`). Don't chase zero; compare against `git stash` to see only what
  your change added. The `ruff --fix` PostToolUse hook clears the auto-fixable
  ones on files you touch.
- Dashboard: `npm --prefix dashboard run build` must succeed; `npx tsc --noEmit`
  for types.
- Line-ending warnings on commit are a Windows/`.gitattributes` gap, harmless.
- Tests must not leak real side effects: `tests/conftest.py`'s autouse fixture
  clears `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` for every test — never re-set
  them (only `tests/test_notify.py` does, deliberately) or a real `pytest` run
  sends real Telegram messages through whatever bot token is in `.env`.

## Money path — extra care

`executor.py`, `dhan_orders.py`, `exit.py`, live arming in `config.py`
(`arm_live_trading` / `set_trading_mode`), the cost model (`charges.py`,
`market_context/spread_calib.py`), `index_ai/risk_manager.py` (one live-loss
budget across India + crypto — `TIGHTENED` drops India orders to 1 lot,
`STOPPED` trips both `risk.kill_switch_state` and `crypto.executor.kill_switch`),
`index_ai/trailing.py` and `index_ai/strategies/credit_spread.py`
(`SELL_TRAIL_POINTS`) — the live stop/trail logic, and `index_ai/scanner.py`'s
tick-driven stop triggering (`on_index_tick`, needs `ENABLE_TICK_FEED=true`).

- **Trailing stops are now index-point-based, not percent-of-premium**
  (Richard, 2026-09-24/28): the stop starts a fixed number of index points
  from entry and moves 1:1 with the index. Buys: NIFTY 25 / BANKNIFTY 55 /
  SENSEX 80 (`instruments._buy_scalp_trail`, activation 0 — scalp, no wide
  initial stop). Sells: NIFTY 40 / BANKNIFTY 100 / SENSEX 130
  (`credit_spread.SELL_TRAIL_POINTS`). The old percent-of-premium trail was
  deleted in Phase 3 (2026-10) — the 1:1 index trail is the only trailing stop
  for both lanes.
  Crypto has its own separate point trail, `crypto/strategies/trailing.py`
  (`point_trail_pct`, default 1.6% of entry price).

- **Two independent locks arm real orders**: `TRADING_MODE=LIVE` *and*
  `ALLOW_LIVE_TRADING=true`. Both are read in `_build_risk_settings`. Arming
  needs the exact phrase `ARM LIVE ORDERS`; switching to Paper always disarms.
- **Never put blocking I/O in an `async def` handler** — a sync SQLite write or
  `.env` write stalls the whole event loop and every concurrent dashboard poll.
  Use `asyncio.to_thread`. This has bitten twice.
- Read-modify-write on a shared file or the lots setting needs a lock, and the
  client should send an absolute value, not a delta.
- The futures paper lanes default ON (`ENABLE_FUTURES_PAPER` /
  `ENABLE_STOCK_FUTURES_PAPER`); paper cannot send an order regardless. The index
  options paper/live lane is the legacy `planner` → `executor` path (see below);
  the old `options_cpr` paper lane was retired — `options_cpr/` is backtest-only.

## Strategy state (don't relitigate — but see 2026-09 cost-model correction below)

Historically every intraday config tested net-negative after real costs, and
naked option buying showed no directional edge. See `memory/strategy-findings.md`
for the full history (the option backtest uses a Black-Scholes proxy — relative
comparisons only, never absolute rupees).

**2026-09-24 correction — this changed the picture:** the live cost model's
slippage estimate (`charges.round_trip_slippage_rupees`) used fixed guessed
half-spreads that were 3-7x the real measured bid-ask, overstating every
India trade's cost by roughly ₹100-200. Fixed to read the measured spread
(`market_context/spread_calib.py`). After the fix, NIFTY credit selling is
**net positive** on the real journal (+₹1,016 over 9 trades, previously shown
as a loss) — so "every config is net-negative" is no longer the settled
conclusion for NIFTY sells specifically; don't cite the old blanket claim
without re-checking `strategy_performance.strategy_scorecard()` against
current data. BANKNIFTY and SENSEX sells are still weaker; a live
per-(strategy, instrument) read is in `memory/strategy-findings.md`'s
newest entries and the Strategy P&L dashboard tab.

`options_cpr/viability.py` scores each index's measured gross edge against its
measured cost floor — this predates the slippage fix above and should be
re-run before being trusted (`scripts/measure_viability_gross.py`).
BANKNIFTY's option book is ~20× NIFTY's, so 4-leg structures never work there
no matter the tuning.

**Strategy lab (`index_ai/strategy_lab.py`), added 2026-09-24 — the current way
new ideas get proven or killed.** Paper-trades ~10 candidate strategies side
by side on the *real* recorded option chain (`market_log.chain` — strikes near
spot, real bid/ask/IV, recorded every scan since 2026-09-23; no more
Black-Scholes proxy for anything built after that date), each priced with real
Dhan charges and the real measured slippage above. `GET /api/strategy-lab`
serves it; each candidate gets a verdict — `COLLECTING` (<30 trades or <14
days), `PASSING`, `DROPPED` — once enough data exists. This is a different
system from the live legacy engine below: it never places an order, it only
tells you which candidate is worth wiring in. Don't confuse a "PASSING" lab
verdict with something already live.

## Which Indian-options engine

The **legacy path** — `scanner._scan_index` → `planner.plan_instrument` →
`strategy_router` / `sell_strategy` / `strategy_mode` → `plan_builder` →
`executor.execute_plan` → `dhan_orders` → `learning` SQLite — is the **one**
Indian-options engine: it places live orders and feeds Trade History / Reports /
`brain`. `index_ai/strategies/options_cpr/` is **backtest-only tooling** now
(`scripts/backtest_options_cpr.py`); its paper lane was retired 2026-09-09. Any
change to how index options trade goes in the legacy modules. See
`memory/indian-options-engine.md`.

## Other sections (own scan task, own journal, paper only)

- **`crypto/`** — Delta Exchange perps, 24/7, its own strategies + ML. Evening
  entry window (`CRYPTO_SESSION_*`, default 16:00–06:00 IST). Gold (PAXGUSD,
  XAUTUSD) removed from `CRYPTO_ALLOWLIST` 2026-09-25 — lost for nearly every
  strategy while every real crypto coin was net positive; don't re-add without
  re-measuring. **Arming crypto live does not send every strategy/coin live**:
  `strategy_performance.crypto_live_pairs()` gates it to (strategy, coin) pairs
  that individually cleared the readiness bar (30+ trades/14+ days net-positive
  for the strategy, and that specific coin net-positive over 5+ trades) —
  `crypto/lanes.py` checks this per entry. Everything else keeps paper-trading
  alongside even when armed.
- **`commodities/`** — MCX mini/micro futures (crude/gas/gold/silver), 09:00–23:30
  IST, runs the evening the equity scanner is shut. **Reuses the index directional
  signal** (`index_ai.strategies.futures.engine`), not the crypto strategies.
  Front contract rolls monthly: `python -m scripts.fetch_commodity_universe`.
  See `commodities/README.md`. `ENABLE_COMMODITIES_PAPER`, never wired to orders.
- **`index_ai/market_holidays.py`** — the NSE/MCX holiday calendar so the
  Indian, futures and commodities lanes go quiet on real holidays, not just
  weekends. Hardcoded per year — refresh it every January against the new
  NSE/MCX circular.

## Reference

- `reference/openalgo/` — vendored submodule, read-only. **Check it before
  implementing any Dhan protocol detail** (the websocket packet layout came from
  there; one field ordering is genuinely counterintuitive).
- Codebase questions: `graphify query "<question>"` on the existing graph rather
  than rebuilding. Exclude `reference/` if you do rebuild.
- Longer context: `memory/` (project vision, charges, options direction).

## Destination

This is headed for **subscription distribution to other traders**, so:
multi-tenancy is the architecture constraint (currently single-`.env`,
single-SQLite, module-global state), and distributing algos in India is
SEBI-regulated. Raise both whenever distribution work comes up. Requiring a
customer to hand-edit `.env` is not an acceptable setup path for any feature.
