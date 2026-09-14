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
  excluded from that path on purpose.
- The server must be restarted to pick up an `.env` change.
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

- `python -m pytest -q` — 353 tests, ~30s. Keep it green.
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
(`arm_live_trading` / `set_trading_mode`), and the cost model (`charges.py`,
`market_context/spread_calib.py`).

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

## Strategy state (don't relitigate)

Every intraday config tested is net-negative after real costs. **Friction, not
signal quality, is the binding constraint.** Naked option buying has no
directional edge at all. Directional selling has a small real gross edge that
4-leg friction eats. Multi-day holding failed too. See
`memory/strategy-findings.md` for the durable conclusions and which numbers are
trustworthy (the option backtest uses a Black-Scholes proxy — relative
comparisons only, never absolute rupees).

`options_cpr/viability.py` scores each index's measured gross edge against its
measured cost floor. The sell-lane gross was re-measured from the real journal
2026-09-09 — NIFTY +₹6, BANKNIFTY −₹178, SENSEX −₹57 per trade, i.e. **no live
gross edge** (the +₹200–300 backtest numbers were BS-proxy optimism). The
`entry_guard._viable_sell_blocks` gate that would pause a NOT_VIABLE index ships
**default-off** (`OPTIONS_REQUIRE_VIABLE`) because the signal was just retimed to
5m/15m; re-run `scripts/measure_viability_gross.py` after ~30 forward trades and
decide. BANKNIFTY's option book is ~20× NIFTY's, so 4-leg structures never work
there no matter the tuning.

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
  entry window (`CRYPTO_SESSION_*`, default 16:00–06:00 IST).
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
