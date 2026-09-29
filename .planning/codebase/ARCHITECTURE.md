<!-- refreshed: 2026-09-30 -->
# Architecture

**Analysis Date:** 2026-09-30

## System Overview

Algo BNF is a three-lane algorithmic options and futures trading platform:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI Server                                  │
│                            `index_ai/server.py`                              │
│  (HTTP API, lifespan, task scheduling, static file serving, auth gateway)    │
└────────┬──────────────────────────┬──────────────────────────┬───────────────┘
         │                          │                          │
         ▼                          ▼                          ▼
  ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
  │  INDEX OPTIONS LANE │   │   CRYPTO LANE       │   │  COMMODITIES LANE   │
  │  (Dhan NSE/BSE F&O) │   │  (Delta Exchange)   │   │   (Dhan MCX)        │
  │  9:15–15:30 IST     │   │   24/7 IST          │   │   09:00–23:30 IST   │
  │                     │   │                     │   │                     │
  │  Scanner → Planner  │   │  Lanes → Executor   │   │  Lanes → Executor   │
  │  → Executor         │   │  → Journal          │   │  → Journal          │
  │  → Learning         │   │  → ML (own model)   │   │  (paper only)       │
  │  → Trailing         │   │                     │   │                     │
  │  (3 indices)        │   │  (BTC, ETH, SOL...) │   │  (CRUDE, GOLD...)   │
  └─────────┬───────────┘   └──────────┬──────────┘   └──────────┬───────────┘
            │                          │                         │
            ▼                          ▼                         ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │                     Data Layers & Persistence                      │
   │                                                                    │
   │  SQLite:  `memory/trade_memory.sqlite` (index lane journal)        │
   │  JSONL:   `memory/crypto_journal.jsonl` (crypto lane trades)       │
   │  Candles: `memory/candles/` (cached intraday + historical)        │
   │  Models:  `memory/models/` (scikit-learn, Hugging Face scores)    │
   │                                                                    │
   └────────────────────────────────────────────────────────────────────┘
            │
            ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │                    React/Vite Dashboard                            │
   │                     `dashboard/src/`                               │
   │  (Live monitor, control panel, strategy P&L, settings, ML tuning)  │
   └────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **Scanner** | Polls Dhan on a 90-second cadence for three indices; evaluates both buy and sell opportunities per index | `index_ai/scanner.py` |
| **Planner** | Fetches candles, option chains; evaluates technical signals (EMA, CPR, candlestick patterns) for dual buy+sell opportunities | `index_ai/planner.py` |
| **Strategy Router** | Routes signal to buy lane, sell lane, or credit spread engine; gates by venue, instrument, mode | `index_ai/strategies/strategy_router.py` |
| **Buy Strategy** | Evaluates candlestick patterns, OI walls, fake breakout detection for buy entries | `index_ai/strategies/buy_strategy.py` |
| **Sell Strategy** | CPR regime, EMA, OI max-pain alignment, S&R from OI profiles for directional sell setup | `index_ai/strategies/sell_strategy.py` |
| **Credit Spread** | Constructs 2-leg put/call spreads (hedge + main); computes width, debit, entry/exit logic | `index_ai/strategies/credit_spread.py` |
| **Executor** | Validates trade against risk gates, ML model, confidence thresholds; creates execution plan; records in journal | `index_ai/executor.py` |
| **Dhan Orders** | Translates execution plan to Dhan broker API calls; handles GTT orders, multi-leg fills, cancellations | `index_ai/dhan_orders.py` |
| **Exit** | Closes positions on trailing-stop hit, profit-target, manual close, or stale-trade age gate | `index_ai/exit.py` |
| **Trailing** | Manages live point-based trailing stop and trailing profit for open positions; runs on a fast 20s loop | `index_ai/trailing.py` |
| **Learning** | SQLite trade journal; feedback loop; scikit-learn win-probability model; Hugging Face sentiment scoring | `index_ai/learning.py` |
| **Market Log** | Snapshots option chains per expiry per scan; feeds the strategy lab | `index_ai/market_log.py` |
| **Crypto Lanes** | Parallel 24/7 strategy engine for Delta Exchange BTC/ETH perps and options; own ML, own journal | `crypto/lanes.py`, `crypto/executor.py` |
| **Commodities Lanes** | Evening MCX futures scanner using index directional signal; paper-only; rolls front contract | `commodities/lanes.py` |
| **Risk Manager** | Checks daily loss cap, max consecutive losses; kills all live orders on breach | `index_ai/risk_manager.py` |
| **Config** | .env loader with caching, settings() factory, arming ceremony gates (TRADING_MODE + ALLOW_LIVE_TRADING) | `index_ai/config.py` |
| **Dashboard API** | HTTP handlers for all UI operations: mode switch, settings, historical data, ML retraining | `index_ai/server.py` |
| **Dashboard UI** | React + Recharts live monitoring, trade history, P&L by strategy/instrument, ML tuning controls | `dashboard/src/` |

## Pattern Overview

**Overall:** Multi-lane algorithmic trading platform with per-lane independent operation plus a unified control plane.

**Key Characteristics:**
- **Async event loop** with background tasks (scanner, crypto, commodities, tick feed, ML training, EOD reports)
- **Defensive gates** at every money-path step: confidence, ML win-prob, max-draw, risk regime tightening
- **Trade-first journal** — every plan is recorded before Dhan order, so paper trades exist even if broker fails
- **Real-world cost model** — charges.py reads actual Dhan fee schedule + measured bid-ask per instrument
- **Session awareness** — market-clock checks NSE/MCX holidays; separate 24/7 crypto session

## Layers

**API & Control (HTTP):**
- Purpose: Accept commands from dashboard, serve live state (trades, scanner status, settings)
- Location: `index_ai/server.py`
- Contains: FastAPI app, route handlers, lifespan management, JSON serialization
- Depends on: config, learning, strategies, dhan_orders
- Used by: dashboard (React), CLI scripts

**Strategy Evaluation:**
- Purpose: Technical analysis, signal generation, opportunity detection
- Location: `index_ai/strategies/`, `index_ai/planner.py`
- Contains: Candlestick patterns, EMA, Supertrend, CPR, OI profiles, breakout/reversal logic
- Depends on: Pandas DataFrames (candles), option chain data
- Used by: scanner, planner

**Execution Gate & Risk:**
- Purpose: Validate signal against risk policy, confidence gate, ML model, arming ceremony
- Location: `index_ai/executor.py`, `index_ai/execution_safety.py`, `index_ai/risk_manager.py`
- Contains: Check gates, compute min-confidence, acquire per-instrument lock, validate option strike range
- Depends on: config, learning, strategy_params, dhan client (for verification)
- Used by: scanner (on execute decision)

**Broker Interface:**
- Purpose: Translate execution plans to broker API calls; track fills and cancellations
- Location: `index_ai/dhan_orders.py`, `index_ai/dhan.py`, `index_ai/dhan_auth.py`
- Contains: Order submission, GTT logic, multi-leg coordination, token refresh
- Depends on: httpx (HTTP client), config
- Used by: executor, exit, manual close handlers

**Journaling & Learning:**
- Purpose: Record every trade; score entries using ML; feedback loop for tuning
- Location: `index_ai/learning.py`, `index_ai/ml_outcomes.py`, `index_ai/hf_learning.py`
- Contains: SQLite schema, trade recording, win-prob model, sentiment scoring, learned-settings storage
- Depends on: sqlite3, scikit-learn, huggingface-hub
- Used by: executor (pre-entry scoring), exit (outcome recording), dashboard (retraining)

**Position Management:**
- Purpose: Monitor and close open trades via trailing stops, profit targets, manual intervention
- Location: `index_ai/trailing.py`, `index_ai/exit.py`, `index_ai/position_exits.py`
- Contains: Trail-point computation, stop trigger logic, exit reason classification
- Depends on: learning (current position state), dhan_orders (close logic)
- Used by: scanner (on_index_tick for fast trail checks), manual UI close buttons

**Market Data & Context:**
- Purpose: Fetch candles, maintain option chain cache, track market regime
- Location: `index_ai/dhan.py`, `index_ai/candle_cache.py`, `index_ai/options_oi.py`, `index_ai/market_context/`
- Contains: DhanClient (HTTP calls), local candle caching, OI analysis (max pain, wall strikes)
- Depends on: httpx, Pandas
- Used by: planner, strategies

## Data Flow

### Primary Request Path (Scanner Loop → Trade Execution)

1. **Boot** (`server.py:lifespan`) → calls `schedule_boot_auto_start()` which queues scanner startup
2. **Scanner Start** (`scanner.py:run_scanner`) → initializes market-clock checks, pre-open brief
3. **Scan Cycle** (`scanner.py:_scan_cycle`) → iterates configured indices (NIFTY, BANKNIFTY, SENSEX)
4. **Fetch Data** (`planner.plan_instrument`) 
   - Calls `DhanClient.intraday_history()` for 5 days of candles at CANDLE_INTERVAL_MINUTES
   - Calls `DhanClient.expiry_list()` → `pick_nearest_expiry()`
   - Calls `DhanClient.option_chain()` to get strike premiums and OI
5. **Evaluate Signals** (`strategy_router.evaluate_dual_opportunities`)
   - Computes EMA, CPR, Supertrend on the candle frame
   - Evaluates buy signal via `buy_strategy.evaluate_buy_signal()` (candlestick patterns + OI walls)
   - Evaluates sell signal via `sell_strategy.evaluate_sell_signal()` (CPR regime + max-pain + OI alignment)
   - Returns StrategySignal with action, confidence, regime
6. **Build Option** (`plan_builder.build_opportunity`)
   - Picks strike from option chain based on signal direction and strategy mode
   - Constructs option dict with legs, spreads, strikes
7. **Create Execution Plan** (`executor.build_execution_plan`)
   - Scores setup against ML model (`ml_outcomes.score_trade_setup`)
   - Checks min-confidence gate, ML win-prob gate
   - Checks loss-guard (per-setup consecutive-loss limit)
   - Returns ExecutionPlan with allowed=True/False and reason
8. **Execute** (`executor.execute_plan`)
   - Acquires per-instrument lock (prevent concurrent entries on same index)
   - Calls `dhan_orders.send_order()` → Dhan API → order confirmation
   - Records trade in SQLite via `learning.record_trade()`
   - Initializes trail metadata (entry price, stop level, profit target)
   - Logs decision event to `market_log`
9. **Open Position Loop** (`scanner.on_index_tick`)
   - Every index tick (or every 20s if tick-feed off), calls `evaluate_open_trade()` per open position
   - Checks if trailing stop or profit target is hit → calls `exit.close_open_trade()`
   - Updates open-trade MTM display

### Exit Path (Trailing Stop / Profit Target / Manual Close)

1. **Trigger** (one of):
   - Trailing stop hit on index tick (`scanner.on_index_tick` → `trailing.evaluate_open_trade`)
   - Profit target hit (same)
   - Manual "Close" button in dashboard (`/api/close-trade` → `exit.close_open_trade`)
   - Stale-trade auto-close (>15 min open without fill, or after 15:10 IST square-off)
2. **Close Trade** (`exit.close_open_trade`)
   - Acquires per-trade exit lock (protect against double-close)
   - Fetches current option LTP via `option_ltp_with_retry()`
   - Computes exit premium, MTM, P&L
   - Submits opposite-direction order to Dhan
   - Records outcome in SQLite (pnl, exit_premium, close_reason)
3. **Learning Update** (`learning.update_learning` via background task)
   - Re-trains ML model if enough closed trades accumulated
   - Adjusts min-confidence gate based on feedback history

### Background Tasks (Lifespan)

- **Auto-refresh Dhan token** (`_auto_renew_loop`): Every 3600s, checks token expiry, renews if needed
- **Candle cache sync** (`_candle_cache_loop`): Every 30m during market hours, syncs 5-day candle cache
- **Tick feed** (`_tick_feed_loop`): WebSocket stream from Dhan; every tick checks/updates open trade trails
- **Crypto scan** (`_crypto_paper_loop`): Every 60s, runs crypto lane regardless of IST market hours
- **Commodities scan** (`_commodities_paper_loop`): Every 60s, runs MCX lane (paused during equity hours)
- **Crypto ML nightly** (`_crypto_nightly_loop`): Once per UTC day, retrains crypto ML model
- **Crypto day summary** (`_crypto_day_summary_loop`): 23:58 IST, sends Telegram recap of open crypto positions
- **Warm thread pool** (`_warm`): On startup, pre-loads commonly used modules to avoid ~2.5s latency on first UI action

**State Management:**
- **module-global** `scanner._state` (ScannerState): Running flag, last cycle, events, pre-open brief, market context
- **module-global** `scanner._health` (ScanHealth): Stage timings, error tracking for ops dashboard
- **module-global** `risk.kill_switch_state` (per-lane kill switches for live orders)
- **SQLite `trade_memory.sqlite`** (index options trades + feedback)
- **JSONL `crypto_journal.jsonl`** (crypto trades)
- **File `memory/crypto_maint_day.txt`** (marker for nightly ML retraining)

## Key Abstractions

**StrategySignal** (`index_ai/strategies/strategy.py`):
- Purpose: Represents a technical signal for one instrument, one lane
- Examples: `BUY_CALL`, `SELL_BULL_PUT_SPREAD`
- Pattern: Named tuple; immutable; carries action, confidence, regime info, reason text

**ExecutionPlan** (`index_ai/executor.py`):
- Purpose: Validated trade plan ready for broker submission or rejection with reason
- Pattern: Immutable dataclass; `allowed` bool gates execution; `reason` explains allow/deny

**Trade Record** (`index_ai/learning.py`):
- Purpose: SQLite row representing one journaled trade (paper or live, open or closed)
- Pattern: UUID trade_id; JSON-stored signal + option; status one of PAPER_RECORDED / LIVE_FILLED / CLOSED; pnl set only on close

**AppSettings** (`index_ai/config.py`):
- Purpose: Immutable runtime configuration from .env
- Pattern: Frozen dataclass; read via `settings()` which caches on .env mtime; used at request boundaries

**OI Context** (`index_ai/options_oi.py`):
- Purpose: Analyzed option chain: max-pain, OI walls per strike, implied implied volatility surface
- Pattern: Computed per scan; passed to both buy + sell strategy evaluators; used to prefer high-OI strikes for S&R

## Entry Points

**Primary Server:**
- Location: `index_ai/server.py:run()` via `python -m index_ai.server`
- Triggers: Manual `python -m uvicorn index_ai.server:app --port 8000` or the launcher script
- Responsibilities: FastAPI app creation, lifespan, route binding, WebSocket upgrades for live updates

**Scanner Task:**
- Location: `scanner.run_scanner()`, scheduled via `schedule_boot_auto_start()` on boot
- Triggers: Queued at server lifespan start (unless `AUTO_START_SCANNER=false`)
- Responsibilities: Initiates the 90-second poll loop, entry/exit decision making, trade execution

**Dashboard:**
- Location: `dashboard/src/App.tsx`
- Triggers: User opens browser to `http://127.0.0.1:8000`
- Responsibilities: Serves static files from `dashboard/dist/`, proxies API calls

## Architectural Constraints

- **Threading:** Single-threaded event loop (asyncio) for I/O; blocking Dhan calls and SQLite writes run on a thread pool via `asyncio.to_thread()`. Scanner and UI never block each other.
- **Global state:** `scanner._state` and `scanner._health` are module-level singletons; `risk.kill_switch_state` is per-lane; accessed from multiple async tasks (requires careful updates).
- **Circular imports:** Avoided by separating concerns into layers (strategies → executor → learning → dhan_orders).
- **Sync/Async boundary:** FastAPI handlers are async; they offload blocking work (Dhan calls, SQLite writes, ML model scoring) to the thread pool via `to_thread()`. Never call blocking I/O directly in async handlers.
- **SQLite WAL mode:** Enabled for multi-reader concurrency; committed at context-manager exit; never left open across async yield.
- **Dhan rate limits:** ~1 request per 3s for option chains; scanner uses INDEX_SCAN_CONCURRENCY (default 1) to serialize index scans; 5s gap between indices in the same cycle.
- **Per-instrument lock:** `execution_safety.acquire_execution_lock()` serializes entries on the same index to prevent double-entry during the window between plan check and Dhan order confirmation.
- **Per-trade lock:** `exit._exit_lock(trade_id)` serializes exits on the same trade to prevent double-close when manual close races automatic trailing stop.

## Anti-Patterns

### Blocking I/O in Async Handlers

**What happens:** A route handler directly calls `sqlite3.connect()` or `DhanClient.intraday_history()` — these block the event loop.

**Why it's wrong:** During that block, all concurrent dashboard polls and ticker updates freeze. A 500ms SQLite write makes the UI "hang" for that duration.

**Do this instead:** Wrap blocking calls in `await asyncio.to_thread(blocking_func, ...)`. See `server.py:_crypto_paper_loop()` for the pattern.

### Shared Mutable Module State Without Locking

**What happens:** Multiple async tasks read/write `scanner._state` without synchronization.

**Why it's wrong:** Race conditions where one task's partial update is read by another before completion; corrupted state visible in the dashboard.

**Do this instead:** Use `threading.Lock()` around state mutations. See `_exit_lock()` in `exit.py` for the pattern. Replace with an asyncio Lock if the critical section is already async.

### Trust Historical Backtest Numbers

**What happens:** `options_cpr/viability.py` scores strategies on Black-Scholes proxy premiums; the backtest shows +₹300/trade but live trades lose.

**Why it's wrong:** The proxy is known to be 3-7x off on slippage assumptions. Relative comparisons (style A vs B) are sound; absolute rupee numbers are not.

**Do this instead:** Always measure on live journal data (`strategy_performance.strategy_scorecard()` over `memory/trade_memory.sqlite` filtered to the current epoch). See `strategy-findings.md` in memory/ for the settled measurement list.

## Error Handling

**Strategy:** Defensive — every decision gate is checked; a failed gate returns a plan with `allowed=False` and a reason, never an exception.

**Patterns:**
- **Dhan network errors:** Retry with exponential backoff via `option_ltp_with_retry()`. If all retries fail, log and fail the exit (position stays open, manual close needed).
- **Missing config:** Logged and caught; the trade is blocked with a clear reason shown in the dashboard (e.g., "SENSEX security id not configured").
- **SQLite locked:** Rare; handled by connection timeout (30s). If timeout, log warning and fail the operation (trade not journaled, plan rejected).
- **Stale data (1 minute old, by IST clock):** Skipped in scanner. Recent candles are always fresh from Dhan.

## Cross-Cutting Concerns

**Logging:**
- `logging.getLogger(__name__)` for module-level loggers; routed to `server.log` and console
- Structured: stage name, event type, instrument, counts, durations via log context fields
- No secrets: token values never logged; only token status

**Validation:**
- Signal confidence must be 0–1
- Option strike must be non-negative
- Risk limits enforced at `_build_risk_settings()` (reads .env, applies policy)
- Every order submission validated by `execution_safety.validate_execution_plan()`

**Authentication & Authorization:**
- No per-user auth (single `.env` instance, single SQLite DB)
- Optional shared-secret gate on money-path endpoints via `admin_auth.require_admin_secret()` (if `ADMIN_API_SECRET` set)
- Dhan auth via JWT + TOTP; auto-refreshed in background

---

*Architecture analysis: 2026-09-30*
