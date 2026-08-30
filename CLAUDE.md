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

## Checks before committing

- `python -m pytest -q` — 353 tests, ~30s. Keep it green.
- `ruff check index_ai/` — **~8 pre-existing cosmetic errors** (unused locals,
  ambiguous `l`). Don't chase zero; compare against `git stash` to see only what
  your change added. The `ruff --fix` PostToolUse hook clears the auto-fixable
  ones on files you touch.
- Dashboard: `npm --prefix dashboard run build` must succeed; `npx tsc --noEmit`
  for types.
- Line-ending warnings on commit are a Windows/`.gitattributes` gap, harmless.

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
- Paper lanes default ON (`ENABLE_OPTIONS_CPR_PAPER` / `ENABLE_FUTURES_PAPER`);
  paper cannot send an order regardless.

## Strategy state (don't relitigate)

Every intraday config tested is net-negative after real costs. **Friction, not
signal quality, is the binding constraint.** Naked option buying has no
directional edge at all. Directional selling has a small real gross edge that
4-leg friction eats. Multi-day holding failed too. See
`memory/strategy-findings.md` for the durable conclusions and which numbers are
trustworthy (the option backtest uses a Black-Scholes proxy — relative
comparisons only, never absolute rupees).

Lanes are gated by `options_cpr/viability.py`: a structure that can't clear its
measured cost floor is blocked. BANKNIFTY's option book is ~20× NIFTY's, so
4-leg structures never work there no matter the tuning.

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
