# Codebase Concerns

**Analysis Date:** 2026-09-30

## Tech Debt

**Multi-tenancy architecture blocker:**
- Issue: The entire codebase assumes single-user operation. Module-level globals (`scanner._state`, `scanner._health`, `scanner._task`), one `.env` file, one SQLite journal (`memory/trade_memory.sqlite`), and global `lru_cache` patterns on charge rates prevent concurrent operation. Distributing to multiple subscribers requires isolation.
- Files: `index_ai/scanner.py` (lines 93-94), `index_ai/config.py` (ENV_PATH), `index_ai/learning.py` (DB_PATH), `index_ai/charges.py` (cached reads)
- Impact: The stated destination (subscriptions to other traders) is architecturally impossible without per-tenant isolation. Every subscriber running side-by-side on the same instance corrupts each other's data and decisions.
- Fix approach: Phase migration toward isolated worker containers per subscriber (as documented in `project-algo-bnf-vision.md`'s phased plan). Immediate steps: thread `tenant_id` through all config/DB lookups, move to Postgres + per-tenant schemas, implement credential vault (AWS Secrets Manager).

**Premium-trail legacy code still in repo but bypassed (resolved 2026-10, Phase 3):**
- Resolved: deleted; the sell-lane exit skip it fed now keys on `credit_spread.SELL_TRAIL_POINTS` (`position_exits._index_trailed_credit`); history preserved in `03-02-SUMMARY.md`.
- Issue (as originally recorded): `premium_trail.py` implements percent-of-premium trailing stops. This was the original strategy (2026-09-24/28 Richard switched to index-point-based trails). The old file still exists and is still imported in some paths, but the live logic uses `credit_spread.SELL_TRAIL_POINTS` (index points) and `instruments._buy_scalp_trail` (also index points) instead. Having two contradictory implementations risks someone re-enabling the wrong one.
- Files: `index_ai/premium_trail.py` (entire file), `index_ai/strategies/credit_spread.py` (lines 346, 388, 397 — SELL_TRAIL_POINTS), `index_ai/instruments.py` (_buy_scalp_trail)
- Impact: A maintenance risk — the premium trail is no longer the truth, but it's still in the codebase where someone might assume it's active or try to switch back to it.
- Fix approach: Delete `premium_trail.py` entirely. It's superseded and keeping it breeds confusion. Verify no other imports reference it first.

**Market holidays hardcoded, yearly refresh needed:**
- Issue: NSE/MCX holiday calendar is hardcoded in `market_holidays.py` per year. The CLAUDE.md notes it "needs a yearly refresh every January against the new NSE/MCX circular." Without this, the scanner runs on holidays it thinks are trading days, causing orphaned positions or missed closes.
- Files: `index_ai/market_holidays.py` (lines 23, full file)
- Impact: In January each year, the holiday dates in the code lag the real NSE/MCX calendar by months until someone manually updates them. Traders can be caught with intraday positions held overnight on a real holiday.
- Fix approach: Add an automated check on startup comparing the hardcoded list against a fetched NSE/MCX calendar (or at minimum, a prominent reminder in the Setup tab that holidays need refreshing). A TODO comment was added (line 23: "ponytail: it needs a manual refresh each year") — make this a scheduled seasonal task.

**Option chain data only from 2026-09-23 onward:**
- Issue: `strategy_lab.py` and the strategy-scoring system rely on recorded real option chains from `market_log.chain`. This data only exists from 2026-09-23 onward (`market_log.py`). Any strategy verdict (PASSING/DROPPED/COLLECTING) built before that date is based on a Black-Scholes proxy and unreliable. Backtests before the cutover should not be trusted for absolute P&L.
- Files: `index_ai/strategy_lab.py` (lines 1-22), `index_ai/market_log.py` (chain table init date)
- Impact: Historical strategy performance numbers before 2026-09-23 used the inaccurate BS proxy. Someone re-reading old backtest reports without this context will draw wrong conclusions. Strategy viability scores (`options_cpr/viability.py`) also predate this fix and should be re-run (noted in CLAUDE.md).
- Fix approach: Document the cutover date prominently in the strategy lab endpoint response. Add a warning in any dashboard display of pre-2026-09-23 backtest results. Re-run `scripts/measure_viability_gross.py` to get current gross-edge measurements with real data.

## Known Bugs

**Tick-driven stop logic fragile and undocumented:**
- Symptoms: The live tick-driven stop triggering in `scanner.py::on_index_tick` (which closes positions on real-time ticks) only works if `ENABLE_TICK_FEED=true`. Without the tick feed enabled, stop losses rely on the candle-bar scanner loop (which may miss ticks). If the Dhan websocket tick stream is slow or falls behind, stops may not fire at the right price.
- Files: `index_ai/scanner.py` (on_index_tick handler), `index_ai/trailing.py` (evaluate_open_trade uses ticks if available)
- Trigger: Set `ENABLE_TICK_FEED=true`, run live, then kill the Dhan websocket or let it lag behind by 10+ seconds.
- Workaround: Fall back to candle-based stops (the default if ticks aren't flowing). Tick-driven is an optimization, not a requirement.

**Crypto pairs gate logic not documented in UI:**
- Symptoms: When the user arms crypto live, some strategies/coins don't actually send orders — they stay paper. The dashboard does not show which (strategy, coin) pairs are live vs paper after arming.
- Files: `crypto/lanes.py` (check against `strategy_performance.crypto_live_pairs()` per entry), `strategy_performance.py` (crypto_live_pairs function)
- Trigger: Arm crypto live, then notice that some strategies aren't opening positions even though they're running.
- Workaround: Check the server logs or call the readiness API endpoint to see which pairs cleared the gate.
- Resolved 2026-10 (Phase 4): the Crypto tab lists every (strategy, coin) pair the lane trades as LIVE or PAPER right under the arm button, with a plain reason for each paper pair, from GET /api/crypto/live-pairs — the same rule crypto/lanes.py uses (live = armed and pair in crypto_live_pairs()).

## Security Considerations

**Authentication gap: ADMIN_API_SECRET is optional:**
- Risk: The FastAPI API has no per-user authentication. Five endpoints that can arm live orders or write Dhan credentials have an optional shared-secret gate (`ADMIN_API_SECRET`), but it defaults off for backward compatibility (running on Richard's Tailscale network). If `.env` does not set the secret, the gate is a no-op and anyone on the network can arm/disarm orders.
- Files: `index_ai/admin_auth.py` (lines 1-33), `index_ai/server.py` (endpoints checking `require_admin_secret`)
- Current mitigation: The server only listens on `127.0.0.1` by default (local machine), and TrustedHostMiddleware blocks DNS-rebinding. Cloud deployments have `DASHBOARD_PASSWORD` (HTTP Basic) as a second gate. But both gates are optional.
- Recommendations: (1) When moving to production/cloud, make `ADMIN_API_SECRET` mandatory for any endpoint that moves money. (2) Build per-user auth (part of the multi-tenancy roadmap) so each subscriber has isolated credentials. (3) Audit which endpoints currently check `require_admin_secret` — ensure all money-path endpoints are gated.

**`.env` secrets at rest:**
- Risk: Dhan access tokens, API keys, and API secrets live in plaintext `.env`. If the machine is compromised or a cloud backup is leaked, all broker credentials are exposed.
- Files: `.env` (contains DHAN_ACCESS_TOKEN, DHAN_API_KEY, DHAN_API_SECRET, TELEGRAM_BOT_TOKEN, potentially WORKER_TOKEN)
- Current mitigation: `.env` is gitignored. Tokens are temporary (Dhan JWT expires, Telegram bot keys can be revoked).
- Recommendations: Migrate to a credential vault (AWS Secrets Manager in the cloud migration plan, or self-hosted encrypted store) before multi-tenant deployment. Never store long-lived secrets in .env once other subscribers are involved.

**HTTP Basic password gate not enforced locally:**
- Risk: `DASHBOARD_PASSWORD` (HTTP Basic auth) is added on 2026-09-28, but only takes effect when `PUBLIC_DEPLOY=true`. During development and local testing, the password is often not set, leaving the API open.
- Files: `index_ai/server.py` (server startup logic checks PUBLIC_DEPLOY)
- Current mitigation: The Tailscale network and `TrustedHostMiddleware` provide perimeter defense.
- Recommendations: In development, still strongly encourage setting `DASHBOARD_PASSWORD` even if not enforced, so workflows are tested as they would be in production.

## Performance Bottlenecks

**Large monolithic files create single points of contention:**
- Problem: `index_ai/server.py` (2,125 lines), `index_ai/learning.py` (1,601 lines), `crypto/lanes.py` (1,148 lines) are the largest files. Any change to a widely-used function (e.g., a learning operation) requires understanding and testing a lot of code. Database writes to the trade journal (`learning.py`) are synchronous (wrapped in `asyncio.to_thread` in handlers, but still blocking the thread pool).
- Files: `index_ai/server.py`, `index_ai/learning.py`, `crypto/lanes.py`
- Cause: Single files grew as features were added without refactoring into smaller modules.
- Improvement path: Break into focused modules by responsibility (e.g., `learning_trades.py`, `learning_outcomes.py`, `server_endpoints_admin.py`). Batch journal writes or use async I/O (SQLite has async drivers).

**Market context (bid-ask spreads, IV) re-computed on every plan:**
- Problem: `market_context/spread_calib.py` reads measured spreads from disk (`SAMPLES_PATH`) every time a strategy plan is built. During heavy trading (every scan loop), this is redundant I/O.
- Files: `index_ai/market_context/spread_calib.py`, `index_ai/charges.py` (calls into spread_calib)
- Cause: Design chose simplicity over caching; the measure is stable within a session.
- Improvement path: Load spreads once at session start and refresh on a timer (e.g., every 5 minutes) rather than on every plan.

## Fragile Areas

**Risk manager cross-section state:**
- Files: `index_ai/risk_manager.py` (account_day, stopped functions)
- Why fragile: A single daily loss budget spans both India (₹) and crypto (USD converted to ₹), with shared kill switches. If the USD/INR conversion factor (`CRYPTO_USDINR`) is stale, the risk calculation drifts. If one lane's loss read is delayed or corrupted, it affects the other lane's risk state.
- Safe modification: Always test that both India and crypto risk changes are reflected in the daily-budget view. When modifying the USD/INR conversion, verify it matches the market rate at the moment of check.
- Test coverage: Risk manager is lightly tested; most coverage is in `test_exit_credit.py` and integration tests. No dedicated unit tests for the cross-section risk logic.

**Trailing-stop logic depends on index-point direction constants:**
- Files: `index_ai/strategies/credit_spread.py` (SELL_TRAIL_POINTS, line 346), `index_ai/strategies/instruments.py` (_buy_scalp_trail)
- Why fragile: The trailing-stop points (NIFTY 40 / BANKNIFTY 100 / SENSEX 130 for sells, different values for buys) are hardcoded magic numbers. They were changed 2026-09-24/28 from percent-of-premium to absolute index points. If a new index is added or the markets' behavior changes, these numbers need re-tuning, but there's no monitoring or alert if they're no longer appropriate.
- Safe modification: Any change to trail points requires re-backtesting against recent live journal data to confirm win rate and stop-hit frequency don't regress. Test both the main ladder and adjacent values to detect one-off tuning.
- Test coverage: Trailing logic is tested in `test_trailing.py` and `test_exit_credit.py`, but with synthetic scenarios, not against live data.
- Monitoring added 2026-10 (Phase 3): index_ai/exit_recheck.py re-checks every segment's stop daily after the close and on demand (Strategy P&L tab, Stop check), sends one Telegram message when a stop's hit rate or win rate moves more than 15 points from its last check, and only ever suggests a new distance for a human to approve.

**Strategy lane eligibility gates are per-pair, not per-lane:**
- Files: `strategy_performance.py` (crypto_live_pairs function), `crypto/lanes.py`
- Why fragile: Crypto strategies only go live if that specific (strategy, coin) pair has cleared the readiness bar (30+ trades, net-positive). But the dashboard's Crypto P&L view doesn't clearly show which pairs are gated. A user might think a strategy is off when it's actually running (on a different coin). Adding a new strategy requires waiting for 30+ trades to accumulate before any pair can go live, even if the strategy works on half the coins.
- Safe modification: When adding a new strategy, create it as paper-only from day one, accumulate 30 trades, and verify the win rate against the live journal. Don't rely on backtest numbers to predict readiness. Document the gating logic in any endpoint that lists live strategies.
- Test coverage: `test_crypto_lanes.py` has basic coverage; readiness gates are tested lightly.

**Option-chain snapshot freshness not enforced:**
- Files: `strategy_lab.py` (lines 66: _NEXT_MAX_AGE_S = 180)
- Why fragile: The strategy lab reads snapshots from `market_log.chain`. A snapshot older than 180 seconds is considered stale and skipped. But there's no alert if the entire chain is stale (e.g., the Dhan connection dropped). The lab will quietly stop trading if chain data stops flowing.
- Safe modification: Add logging on every strategy-lab scan to confirm the age of the newest snapshot. Alert if no snapshot is younger than 5 minutes.
- Test coverage: Strategy lab has unit tests but no live-data integration tests that verify behavior under stale or missing chain data.

## Scaling Limits

**Single-instance enforcement prevents horizontal scaling:**
- Current capacity: One instance per `.env` + SQLite. The `single_instance.py` lock ensures only one server process accesses that database at a time.
- Limit: Cannot run load-balanced or multi-region deployments. A second instance sharing the same .env and DB will exit immediately.
- Scaling path: Move to Postgres + file-based leasing (or distributed lease service like DynamoDB locks) to allow multiple instances. This is part of the cloud migration plan.
- Files: `index_ai/single_instance.py`, `index_ai/server.py` (lifespan, calls acquire_or_exit)

**SQLite journal is single-writer, many-readers:**
- Current capacity: One writer (the scanner) and many concurrent readers (dashboard polls). SQLite handles this via locking, but with heavy concurrent dashboards (10+), contention rises.
- Limit: Beyond ~10-15 concurrent dashboard users, latency climbs sharply as readers wait for locks.
- Scaling path: Migrate to Postgres (supports true concurrency). The trade journal would become `trades` table in Postgres; market log would be `market_log_entries`. This is part of the cloud migration.
- Files: `index_ai/learning.py` (DB_PATH), `index_ai/market_log.py` (chain table)

## Dependencies at Risk

**Black-Scholes proxy option-pricing for older backtests:**
- Risk: The option backtest uses a Black-Scholes proxy to estimate premiums. The proxy has been tuned/revised six times with wildly different results (from +₹700k to −₹1.2M on the same strategy). Backtests before 2026-09-23 are unreliable for absolute P&L; relative comparisons only.
- Impact: Anyone re-reading a strategy backtest from August or earlier might make wrong decisions.
- Migration plan: All new strategies are evaluated on real recorded option chains (from `market_log.chain`, available since 2026-09-23). Old backtests are archived, not retro-fitted. Use `scripts/backtest_options_cpr.py` with real data only.

**Measured spread sampling may be incomplete:**
- Risk: The live cost model reads measured bid-ask spreads from `memory/spread_samples.jsonl`. If spread sampling was disabled (`ENABLE_SPREAD_SAMPLING=false`) during certain hours, the sample set has gaps.
- Impact: Costs calculated during a gap period revert to fixed guesses, which may be stale.
- Migration plan: Ensure spread sampling runs all market hours. A scheduled check could alert if the daily sample count is below a threshold.

## Missing Critical Features

**No per-strategy or per-coin kill switch:**
- Problem: If a single strategy starts losing heavily, the only options are to pause the whole scanner or tighten the account-wide risk gate. There's no "stop this strategy only" control except manual dashboard disarm.
- Blocks: Can't easily A/B test a new strategy variant alongside the live one; if one goes wrong, both shut down.
- Implementation sketch: Add a per-strategy kill switch (in `strategy_params.json`), gated in `plan_builder.py` before order execution. UI: one click to toggle per strategy in the Strategy P&L tab.

**Market data debugging dashboard:**
- Problem: Tick feed quality, option-chain staleness, and bid-ask spreads are visible only via logs or direct API calls.
- Blocks: Operators can't quickly diagnose why a trade missed or a stop didn't fire.
- Implementation sketch: Add a "Data Health" dashboard tab showing: last tick age, chain snapshot age per index, measured spread age, Dhan websocket status, exchange latency.
- Resolved 2026-10 (Phase 4): Data health panel at the top of the Index Options page (GET /api/data-health) — live-price age, option-chain age and measured-spread age per index, Dhan live-feed status; amber/red only while the market is open, neutral when closed.

## Test Coverage Gaps

**Money-path edge cases under-tested:**
- What's not tested: Race conditions in order cancellation (what if a cancel order is issued while the order is being placed?), position reconciliation under broker disconnection, stale-trade recovery, capital lock exhaustion mid-session.
- Files: `index_ai/executor.py`, `index_ai/dhan_orders.py`, `index_ai/learning.py` (reconcile functions)
- Risk: A rare race condition in live trading could cause orphaned positions or duplicate fills.
- Priority: High. Add property-based tests (using hypothesis) that replay recorded Dhan API latencies and packet drops.

**Kill-switch interactions untested:**
- What's not tested: Does arming `kill_switch_state == TRUE` (account daily-loss limit hit) reliably prevent India orders AND crypto orders simultaneously? What if they're evaluated on different threads?
- Files: `index_ai/risk.py`, `index_ai/risk_manager.py`, `index_ai/scanner.py` (uses kill_switch_state), `crypto/lanes.py` (checks its own kill switch)
- Risk: A kill switch trigger on one lane might not propagate to the other, leaving money still at risk.
- Priority: High. Add an integration test that simulates hitting the daily-loss limit and confirms both India and crypto lanes stop.

**Dhan websocket reconnection under load:**
- What's not tested: What happens when the Dhan websocket drops during a busy market hour (20+ concurrent candles being scored)? Does the reconnection finish before the next scan cycle, or are candles missed?
- Files: `index_ai/dhan.py` (websocket client), `index_ai/scanner.py` (poll loop)
- Risk: Missed ticks during reconnection could cause stale decision data.
- Priority: Medium. Add a chaos-engineering test that injects websocket drops and verifies the scanner recovers.

**Strategy lab verdicts on thin sample size:**
- What's not tested: Edge case where a PASSING verdict is reached on exactly 30 trades (the minimum). Does a small sample size cause false positives?
- Files: `index_ai/strategy_lab.py` (MIN_TRADES = 30)
- Risk: A strategy might be labeled PASSING with barely enough data to avoid noise.
- Priority: Low-medium. Add a test that verifies the verdict is stable when the next 10 trades arrive (i.e., doesn't flip to DROPPED).

---

*Concerns audit: 2026-09-30*
