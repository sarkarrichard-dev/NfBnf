# Codebase Structure

**Analysis Date:** 2026-09-30

## Directory Layout

```
Algo BNF/
├── index_ai/                      # Indian index options engine (NIFTY, BANKNIFTY, SENSEX)
│   ├── server.py                  # FastAPI app, HTTP handlers, lifespan
│   ├── scanner.py                 # 90s poll loop, entry/exit decisions, event logging
│   ├── planner.py                 # Candle fetch, signal evaluation, opportunity planning
│   ├── executor.py                # Execution plan builder, gate checks, trade recording
│   ├── dhan_orders.py             # Order submission, GTT logic, Dhan protocol
│   ├── dhan.py                    # Dhan HTTP client, chart/chain/LTP calls
│   ├── dhan_auth.py               # JWT + TOTP flow, token refresh, OAuth helpers
│   ├── exit.py                    # Close position logic, trail stop check, P&L computation
│   ├── trailing.py                # Trail stop/profit point tracking, update logic
│   ├── learning.py                # SQLite trade journal, feedback loop, learned settings
│   ├── ml_outcomes.py             # scikit-learn model scoring, feature extraction
│   ├── hf_learning.py             # Hugging Face sentiment scoring, setup narrative
│   ├── config.py                  # .env loader, AppSettings factory, arming gates
│   ├── risk.py                    # Daily loss cap, kill switches, regime tightening
│   ├── risk_manager.py            # Risk limit checks, kill-switch trigger logic
│   ├── admin_auth.py              # Optional shared-secret gate for money endpoints
│   ├── notify.py                  # Telegram message sending (text, charts)
│   ├── market_clock.py            # IST time, session windows, holiday checks
│   ├── market_log.py              # Chain snapshot recording, decision event logging
│   ├── candles.py                 # Frame resampling, EMA/CPR computation, bar prep
│   ├── candle_cache.py            # Local 5-day candle cache sync from Dhan
│   ├── options_oi.py              # Option chain analysis: max pain, wall strikes, IV
│   ├── options_expiry.py          # Expiry date parsing, nearest expiry picker
│   ├── plan_builder.py            # Strike selection, opportunity structure builder
│   ├── strategy_performance.py    # Strategy scorecard, per-(strategy, instrument) P&L
│   ├── strategy_lab.py            # Paper-trade candidate strategies side by side
│   ├── strategy_learning.py       # Learning loop scheduler, retrain triggers
│   ├── position_exits.py          # Exit reason classification (trail, target, stale)
│   ├── mtm.py                     # Mark-to-market computation for open positions
│   ├── analytics.py               # Dashboard analytics: P&L charts, heatmaps
│   ├── reports.py                 # Trade report builder, CSV export
│   ├── reconcile.py               # Trade reconciliation checks
│   ├── dhan_portfolio.py          # Fetch Dhan holdings, filter to our orders
│   ├── daily_ops.py               # End-of-day summary, trade cleanup, EOD brief
│   ├── day_review.py              # Daily summary text generation
│   ├── pre_open_brief.py          # Pre-open analysis, confidence bump
│   ├── scan_health.py             # Stage timing, error tracking, concurrent gather
│   ├── tick_feed.py               # Dhan WebSocket connection, tick stream parsing
│   ├── trade_cleanup.py           # Clean stale/orphaned trade records
│   ├── trade_lots.py              # Per-trade lot count, total deployed capital
│   ├── instruments.py             # NIFTY, BANKNIFTY, SENSEX config + security IDs
│   ├── capital_required.py        # Margin/capital computation for positions
│   ├── charges.py                 # Dhan brokerage, STT, slippage cost model
│   ├── chart_live.py              # Supertrend snapshot fetch for entry display
│   ├── heatmap.py                 # Instrument + strategy heatmap builder
│   ├── ops_status.py              # Scanner, scanner health status
│   ├── single_instance.py         # Lock to prevent concurrent instances
│   ├── env.py                     # Helper to parse env vars (bool, int, float)
│   ├── data_epoch.py              # Data refresh epoch marker (2026-09-10 cutoff)
│   ├── execution_safety.py        # Pre-execution validation, per-instrument lock
│   ├── profit_trail.py            # Profit trailing logic
│   ├── ticker.py                  # Ticker symbol normalization
│   ├── llm.py                     # Claude API calls for setup narrative
│   ├── token_scheduler.py         # Dhan token scheduler
│   ├── cloud_backup.py            # S3 backup (cloud migration future)
│   ├── atomic_io.py               # Atomic file writes (temp + move)
│   ├── dhan_errors.py             # Dhan error classification, friendly messages
│   ├── dhan_network.py            # Network diagnostics
│   ├── oi_learning.py             # OI-based learning (archived/dormant)
│   ├── strategies/
│   │   ├── strategy.py            # StrategySignal, StrategyMode base types
│   │   ├── strategy_router.py     # Route signal to buy/sell/credit evaluator
│   │   ├── strategy_params.py     # Tunable params for all strategies
│   │   ├── strategy_mode.py       # Mode enum (CPREMA, ICHIMOKU, etc.)
│   │   ├── buy_strategy.py        # Candlestick pattern buy entries + OI wall gating
│   │   ├── sell_strategy.py       # CPR + EMA + OI alignment for sell signal
│   │   ├── credit_spread.py       # 2-leg put/call spread logic, width/debit, SELL_TRAIL_POINTS
│   │   ├── oi_credit.py           # OI-profile-based S&R for credit structure builder
│   │   ├── oi_signals.py          # OI-based entry/exit signals
│   │   ├── candlestick_patterns.py # Pattern detection (engulfing, inside bar, etc.)
│   │   ├── candlestick_sr.py      # Trend classification from candles
│   │   ├── cpr_regime.py          # CPR band computation, regime classification
│   │   ├── option_structures.py   # Multi-leg structure builders (straddle, strangle, etc.)
│   │   ├── breakout.py            # Breakout detection logic
│   │   ├── ema_cross.py           # EMA crossover signal
│   │   ├── ichimoku.py            # Ichimoku cloud indicator (shared with crypto)
│   │   ├── supertrend.py          # Supertrend computation
│   │   ├── bar_volume.py          # Volume analysis
│   │   ├── pivot_points.py        # Prior-session pivot levels
│   │   ├── premium_sell.py        # Premium-selling entry filter
│   │   └── futures/
│   │       ├── engine.py          # Stock futures directional strategy
│   │       └── paper.py           # Stock futures paper-trade lane
│   ├── brain/
│   │   └── regime.py              # Market regime classification, tightened state gate
│   ├── market_context/
│   │   └── spread_calib.py        # Measured bid-ask calibration per instrument
│   └── backtest.py, backtest_options.py  # Backtest tooling
│
├── crypto/                        # Delta Exchange 24/7 crypto lane
│   ├── lanes.py                   # Main scan loop, strategy selection per coin
│   ├── executor.py                # Order submission to Delta, fill tracking
│   ├── journal.py                 # JSONL-backed trade journal
│   ├── live.py                    # Live order arming ceremony, kill switch
│   ├── config.py                  # Crypto-specific config (CRYPTO_MEMORY, sizes, etc.)
│   ├── charges.py                 # Delta maker/taker fee model
│   ├── sizing.py                  # Position sizing (margin per position, USD)
│   ├── session.py                 # 24/7 session window (no market_clock concept)
│   ├── candle_cache.py            # Crypto candle caching from Delta
│   ├── day_review.py              # Daily EOD Telegram recap
│   ├── api.py                     # Internal API for dashboard queries
│   ├── backtest.py                # Historical backtest runner
│   ├── btc_straddle.py            # BTC daily options straddle paper lane
│   ├── delta/
│   │   ├── client.py              # Delta Exchange HTTP client
│   │   ├── market_data.py         # Market data parsing
│   │   ├── products.py            # Crypto product definitions
│   │   └── options.py             # Options-specific API calls
│   ├── ml/
│   │   ├── model.py               # Keras/scikit-learn crypto win-prob model (live journal only)
│   │   ├── optimize.py            # Walk-forward backtest-based tuner (manual diagnostic only)
│   │   ├── gate.py                # Readiness thresholds (30+ trades, 14+ days, net-positive)
│   │   ├── features.py            # ML feature extraction
│   │   └── dataset.py             # Training data prep
│   └── strategies/
│       ├── cpr_trend.py           # CPR + EMA + Supertrend (readiness-gated live)
│       ├── ak_roxx_pro.py         # 8-condition confluence (readiness-gated live)
│       ├── ny_n_break.pine        # 6 PM NY N-break strategy (Pine script reference)
│       ├── bb_reversal.py         # Bollinger Band reversal (paper only)
│       ├── ema_jaguar.py          # EMA Jaguar (paper only)
│       ├── ichimoku.py            # Ichimoku cloud (paper only)
│       └── indicators.py          # Shared indicator implementations
│
├── commodities/                   # Dhan MCX commodity futures lane (09:00–23:30 IST, evening)
│   ├── lanes.py                   # Scan loop, reuses index directional signal
│   ├── config.py                  # MCX-specific config (exchange, symbol mapping)
│   ├── charges.py                 # MCX commodity charge schedule
│   ├── session.py                 # MCX session windows (separate from equity)
│   ├── instruments.py             # CRUDEOILM, NATGASMINI, GOLDM, SILVERMIC definitions
│   └── README.md                  # Commodities lane documentation
│
├── dashboard/                     # React/Vite frontend
│   ├── src/
│   │   ├── App.tsx                # Main app, tab layout (Live, History, Reports, etc.)
│   │   ├── main.tsx               # React entry point
│   │   ├── index.css              # Hawk-gold palette, dark theme base
│   │   ├── App.css                # Component-level styles
│   │   ├── components/
│   │   │   ├── DhanAuthPanel.tsx  # Dhan login, token display
│   │   │   ├── ExecutionPanel.tsx # Live order display, manual close buttons
│   │   │   ├── JournalPanel.tsx   # Trade history, filter by instrument/lane
│   │   │   ├── LearningPanel.tsx  # ML model retrain, Hugging Face upload
│   │   │   ├── BrainPanel.tsx     # Learned settings, confidence gate display
│   │   │   ├── StrategyTuningPanel.tsx # Parameter tuning controls
│   │   │   ├── CryptoPanel.tsx    # Crypto live orders, strategy selection
│   │   │   ├── CryptoSetupPanel.tsx # Crypto ML setup readiness
│   │   │   ├── CryptoDayReviewPanel.tsx # Crypto daily EOD recap
│   │   │   ├── CommoditiesPanel.tsx # MCX trades, paper lane status
│   │   │   ├── FuturesPanel.tsx   # Stock futures paper lane
│   │   │   ├── DayReviewPanel.tsx # Index EOD summary, trade cleanup review
│   │   │   ├── OperationsPanel.tsx # Scanner status, health, manual commands
│   │   │   ├── FeaturesPanel.tsx  # Feature toggle switch API
│   │   │   ├── LanesPanel.tsx     # Enable/disable lanes
│   │   │   ├── AutoTraderPanel.tsx # Auto-start scanner toggle
│   │   │   ├── BacktestPanel.tsx  # Run backtest jobs
│   │   │   ├── TradeLogTable.tsx  # Reusable trade table component
│   │   │   ├── PositionRow.tsx    # Single open position row
│   │   │   ├── StatsRail.tsx      # Summary KPI bar (profit, trades, win%)
│   │   │   ├── Sparkline.tsx      # Inline sparkline chart
│   │   │   ├── PeriodBar.tsx      # Period selector (Today, Weekly, etc.)
│   │   │   ├── SourceToggle.tsx   # Paper/Live/Crypto lane toggle
│   │   │   ├── CollapsibleSection.tsx # Collapsible accordion component
│   │   │   ├── charts/
│   │   │   │   ├── P&LChart.tsx   # P&L over time (Recharts area chart)
│   │   │   │   ├── WinRateChart.tsx # Win rate bar chart per instrument
│   │   │   │   ├── StrategyComparison.tsx # Strategy scorecard radar chart
│   │   │   │   └── HeatmapChart.tsx # Instrument × strategy heatmap
│   │   │   ├── pages/
│   │   │   │   └── StrategyLabPage.tsx # Strategy lab candidate display
│   │   │   ├── shell/
│   │   │   │   ├── Header.tsx     # App header, connection status
│   │   │   │   ├── TabNav.tsx     # Tab navigation (Live, History, Reports, Setup)
│   │   │   │   └── StatusBar.tsx  # Footer status: server time, market status, scanner status
│   │   │   ├── strategies/
│   │   │   │   └── StrategyCard.tsx # Strategy description + live status card
│   │   │   ├── ui/
│   │   │   │   ├── Button.tsx     # Styled button component
│   │   │   │   ├── Card.tsx       # Card container
│   │   │   │   ├── Alert.tsx      # Alert/error box
│   │   │   │   └── Input.tsx      # Text input component
│   │   ├── hooks/
│   │   │   ├── useAPI.ts          # HTTP fetch wrapper (auth, error handling)
│   │   │   ├── useLiveUpdate.ts   # WebSocket / poll for live data
│   │   │   ├── useLocalStorage.ts # Persist UI state across refresh
│   │   │   └── useTimezone.ts     # IST time formatting
│   │   ├── lib/
│   │   │   ├── api.ts            # API client (routes, types, error handling)
│   │   │   ├── format.ts         # Number/currency formatting (₹ with commas)
│   │   │   └── math.ts           # Shared math utilities (drawdown, Sharpe, etc.)
│   │   ├── types/
│   │   │   ├── trade.ts          # Trade, OpenPosition, TradeOutcome types
│   │   │   ├── settings.ts       # AppSettings, RiskSettings types
│   │   │   └── strategy.ts       # StrategySignal, ExecutionPlan types
│   │   └── assets/
│   │       ├── logo.svg          # Hawk icon
│   │       └── placeholder.png   # Default chart placeholder
│   ├── dist/                      # Built app (npm run build output) — served by server.py
│   ├── public/                    # Static assets copied to dist
│   ├── vite.config.ts             # Vite build config
│   ├── tsconfig.json              # TypeScript config
│   ├── package.json               # npm dependencies (React, Recharts, Vite)
│   └── README.md                  # Dashboard build instructions
│
├── scripts/                       # Utility scripts (not committed code, one-off tools)
│   ├── backtest_options_cpr.py    # Full-history backtest of CPR+EMA sell lane
│   ├── backtest_stock_futures.py  # Stock futures backtest runner
│   ├── measure_viability_gross.py # Live journal gross edge measurement
│   ├── fetch_commodity_universe.py # Refresh commodity contract definitions
│   └── (other measurement/diagnostic scripts)
│
├── tests/                         # pytest test suite
│   ├── conftest.py                # Fixtures, autouse teardown (clears Telegram env vars)
│   ├── test_notify.py             # Telegram integration tests (only file that re-sets tokens)
│   ├── test_scanner.py            # Scanner logic tests
│   ├── test_learning.py           # SQLite schema, trade recording tests
│   ├── test_strategies/           # Strategy signal evaluation tests
│   └── (660+ tests, ~7-8 min full suite)
│
├── memory/                        # Local state (never committed; .gitignore)
│   ├── trade_memory.sqlite        # SQLite trade journal (index lane)
│   ├── crypto_journal.jsonl       # JSONL trade log (crypto lane)
│   ├── models/                    # scikit-learn pickle files, Hugging Face exports
│   ├── candles/
│   │   ├── NIFTY_1m/, NIFTY_5m/, NIFTY_15m/  # Parquet candle caches
│   │   ├── BANKNIFTY_1m/, ...
│   │   └── SENSEX_1m/, ...
│   ├── crypto_candles/
│   │   ├── BTCUSD_1h/, ETHUSD_1h/, ...
│   │   └── (hourly candle caches for crypto)
│   ├── backtests/                 # Backtest result snapshots
│   ├── daily_reports/             # End-of-day JSON summaries
│   ├── hf/                        # Hugging Face export dir (outcomes.jsonl)
│   ├── investing/                 # Investing lane data (future)
│   ├── archive/                   # Archived journals (dated snapshots)
│   └── server.log                 # Runtime logs (tail here for diagnostics)
│
├── reference/                     # Vendored submodule (read-only)
│   └── openalgo/                  # marketcalls/openalgo — Dhan protocol reference
│
├── research/                      # Old backtest & ML research (not live code)
│   ├── backtests/
│   ├── datasets/
│   ├── options_cpr/
│   ├── futures/
│   ├── stock_futures/
│   └── reports/
│
├── deploy/                        # Docker, AWS config (cloud migration future)
│   └── Dockerfile                 # Container spec
│
├── guides/                        # Plain-language documentation
│   └── (strategy docs, API walkthrough, trader notes)
│
├── .claude/                       # Claude Code configuration
│   ├── skills/                    # Project-specific skills (new-strategy-backtest, pr-check, etc.)
│   ├── hooks/                     # Post-commit, pre-test hooks
│   └── worktrees/                 # Multi-branch development isolation
│
├── .github/workflows/             # CI/CD (if enabled)
│
├── CLAUDE.md                      # Project instructions (this system's constraints)
├── README.md                      # Quick start guide
├── pyproject.toml                 # Python project metadata, ruff config
├── .env.example                   # Template for secrets (.env is permission-blocked)
├── .mcp.json                      # Claude MCP server config (Playwright only, no secrets)
├── .gitignore                     # Excludes memory/, .env, __pycache__, build artifacts
├── Dockerfile                     # Container spec for cloud deployment
├── openapi.json                   # OpenAPI/Swagger spec (generated from routes)
└── START HERE.md, Start Index Options AI.cmd  # Launcher scripts for Windows
```

## Directory Purposes

**index_ai/**
- Purpose: Indian index options trading engine (one-lot per-index CPR+EMA buy/sell with 2-leg credit spreads)
- Contains: All strategy logic, Dhan integration, risk gates, SQLite trade journal
- Key files: `scanner.py` (entry point, 90s loop), `planner.py` (signal generation), `executor.py` (gate checks + recording)

**crypto/**
- Purpose: 24/7 Delta Exchange BTC/ETH perps + straddle strategies, independent ML, independent journal
- Contains: Crypto-specific broker client, market data fetch, per-strategy paper/live lanes, ML training
- Key files: `lanes.py` (scan loop), `executor.py` (Delta order submission), `ml/model.py` (live-journal-only training)

**commodities/**
- Purpose: MCX mini/micro commodity futures (crude, gas, gold, silver), evening cadence, paper-only
- Contains: Commodity product definitions, charge schedule, lane logic
- Key files: `lanes.py` (scan, reuses index directional signal), `instruments.py` (MCX security IDs)

**dashboard/**
- Purpose: React/Vite UI for live monitoring, control, historical reports, ML tuning
- Contains: Tab-based SPA, API client, real-time update hooks, charts
- Key files: `App.tsx` (layout), components (panels for each section), `lib/api.ts` (API wrapper)

**scripts/**
- Purpose: One-off diagnostic, backtest, measurement tools — not part of live code
- Contains: Strategy backtests, cost model tuning, journal analytics
- Key files: `backtest_options_cpr.py` (CPR lane validation), `measure_viability_gross.py` (live P&L audit)

**tests/**
- Purpose: pytest test suite (660+ tests, ~7-8 min full run)
- Contains: Unit & integration tests, fixtures, Telegram token isolation
- Key files: `conftest.py` (autouse teardown), `test_notify.py` (only file re-setting Telegram tokens)

**memory/**
- Purpose: Local runtime state: trade journals, candle caches, ML models, logs
- Contains: SQLite `.sqlite`, JSONL `.jsonl`, Parquet candles, pickled sklearn models
- Key files: `trade_memory.sqlite` (index trades), `crypto_journal.jsonl` (crypto trades), `server.log`

**reference/openalgo/**
- Purpose: Vendored Dhan protocol reference (marketcalls/openalgo submodule) — read-only
- Contains: WebSocket packet specs, Dhan API field ordering, protocol examples
- Usage: Consulted when implementing Dhan protocol details (field byte order, etc.)

## Key File Locations

**Entry Points:**
- `index_ai/server.py`: FastAPI app, all HTTP routes, lifespan management
- `dashboard/src/main.tsx`: React entry point (loads vite, mounts App component)
- `scripts/backtest_options_cpr.py`: Run full-history backtest (`python -m scripts.backtest_options_cpr`)

**Configuration:**
- `.env` (permission-blocked in this session) — Dhan credentials, risk settings, feature flags
- `.env.example` — Template; copy and fill with your secrets
- `index_ai/config.py` — AppSettings factory, settings() call site
- `index_ai/risk_policy.py` — HARDCODED_RISK struct, policy constants
- `crypto/config.py` — Crypto-specific paths, model storage
- `commodities/config.py` — MCX symbol mappings

**Core Logic:**
- `index_ai/scanner.py` — Scanner task, 90s poll loop, signal evaluation, trade execution
- `index_ai/planner.py` — Dual-opportunity planning (buy + sell), signal generation
- `index_ai/executor.py` — Gate checks, plan validation, trade recording
- `index_ai/dhan_orders.py` — Dhan order submission, GTT logic, multi-leg coordination
- `index_ai/exit.py` — Close position, compute P&L, record outcome

**Testing:**
- `tests/conftest.py` — Fixtures (mock Dhan, SQLite in-memory)
- `tests/test_scanner.py` — Scanner logic, opportunity detection
- `tests/test_learning.py` — Trade recording, schema
- `pytest -q` — Run all tests (verify before commit)

**Utilities:**
- `index_ai/market_clock.py` — IST time, session windows, holiday checks
- `index_ai/instruments.py` — NIFTY, BANKNIFTY, SENSEX config + security IDs
- `index_ai/charges.py` — Dhan brokerage + STT + slippage cost model
- `index_ai/notify.py` — Telegram message + chart sending

## Naming Conventions

**Files:**
- `*_spec.py` / `*_test.py` unused; use `test_*.py` (pytest discovery)
- Module files lowercase_with_underscores.py
- Test files `test_*.py` co-located in `tests/` or alongside implementation

**Directories:**
- Package dirs lowercase (no hyphens)
- `memory/` for local state
- `strategies/` for strategy submodules
- `ml/` for machine learning modules

**Functions:**
- Public functions: `lowercase_with_underscores()`
- Private (module-level): `_leading_underscore()`
- Async functions: `async def async_name()`
- Background tasks: `_background_loop_name()`

**Variables:**
- Module-level mutable state: `_state`, `_task`, `_health` (prefixed underscore, marked in comments as global)
- Constants: `ALL_CAPS` (e.g., `SCAN_INTERVAL_SECONDS`, `VALID_CANDLE_INTERVALS`)
- Type hints always used for function parameters and returns

**Types:**
- StrategySignal, ExecutionPlan, AppSettings frozen/dataclass types
- Trade records: dict-based (JSON-serializable)
- Config always immutable (frozen dataclass)

**Tests:**
- `test_*.py` files in `tests/` directory
- Test function `test_feature_behavior()` (descriptive name)
- Fixture `@pytest.fixture def mock_dhan_client():`
- Parametrize: `@pytest.mark.parametrize("param", [val1, val2])`

## Where to Add New Code

**New Index Strategy or Signal:**
- Implementation: `index_ai/strategies/` (e.g., `index_ai/strategies/new_signal.py`)
- Integration: Import in `strategy_router.py`, add to `evaluate_dual_opportunities()` or strategy selection
- Tests: `tests/test_strategies/test_new_signal.py`
- Example: `breakout.py` (detect breakout, used in buy/sell strategy evaluation)

**New Endpoint / Dashboard API:**
- Implementation: Add route handler in `index_ai/server.py` (async, use `asyncio.to_thread()` for blocking I/O)
- Response type: Consistent JSON; error codes 400/403/500
- Tests: Add to `tests/test_scanner.py` or create `tests/test_api.py` if large
- Example: `/api/close-trade` POST handler calls `exit.close_open_trade()`

**New Dashboard Component:**
- Implementation: `dashboard/src/components/NewPanel.tsx` (functional component)
- Styling: Use existing CSS variables (`--hawk-gold`, `--dark-bg`) from `index.css`
- API calls: Use `useAPI()` hook from `hooks/useAPI.ts`
- Tests: Minimal (jest config not set up; verify via browser)
- Example: `ExecutionPanel.tsx` (displays open trades, manual close buttons)

**New Crypto Strategy:**
- Implementation: `crypto/strategies/strategy_name.py`
- Paper lane: Add to `crypto/lanes.py`, gate with feature flag
- ML scoring: Update `crypto/ml/features.py` if new indicators; `crypto/ml/model.py` retrains automatically
- Journal: Trades auto-recorded to `memory/crypto_journal.jsonl` by executor
- Example: `cpr_trend.py` (CPR+EMA+Supertrend, readiness-gated)

**New Commodity Lane:**
- Implementation: `commodities/lanes.py` (update scan loop, add instrument config)
- Charge model: Update `commodities/charges.py`
- Security IDs: Add to `commodities/instruments.py`
- Dashboard: Update `CommoditiesPanel.tsx` if new display needed
- Example: Add NATURALGASM (next nat gas contract) with real MCX security ID

**Database Migration / SQLite Schema Change:**
- Location: `index_ai/learning.py`, `init_db()` function
- Migration: ALTER TABLE inside `db.executescript()` (wrap in CREATE TABLE IF NOT EXISTS for safety)
- Backward compat: Always check for column existence before using new column
- Example: Adding a new `exit_reason TEXT` column to trades table

**Shared Utility / Helper:**
- Location: `index_ai/lib/` or create new file if isolated concern
- Usage: Import in multiple modules, no circular dependencies
- Tests: Add unit tests in `tests/`
- Example: `market_clock.py` (time utilities), `notify.py` (Telegram), `charges.py` (costs)

## Special Directories

**memory/**
- Purpose: Local state, never committed to git
- Generated: Yes (runtime; trades recorded, candles cached, models trained)
- Committed: No (in .gitignore)
- Contents: SQLite `.sqlite`, JSONL `.jsonl`, Parquet `.parquet`, pickled models `.pkl`
- Access: Always via `Path(MEMORY_DIR)` from `config.py`; ensure `mkdir(parents=True, exist_ok=True)`

**reference/openalgo/**
- Purpose: Vendored submodule (git submodule), read-only reference
- Generated: No
- Committed: No (submodule pointer only, not full content in main repo)
- Usage: Consult Dhan protocol details when implementing network code

**research/**
- Purpose: Backtest runs, measurement scripts, archived experiments
- Generated: Yes (script output)
- Committed: No (research artifacts, old analysis)
- Usage: Historical reference only; don't reuse old strategy code

**deploy/**
- Purpose: Docker, Kubernetes, cloud config (cloud migration future)
- Generated: No (hand-written)
- Committed: Yes
- Contents: Dockerfile, docker-compose.yml (future), AWS CloudFormation (future)

**scripts/**
- Purpose: Diagnostic, backtest, measurement tools — one-off utilities
- Generated: Output only (results/ snapshots)
- Committed: Yes (reusable tools)
- Usage: Manual runs (e.g., `python -m scripts.backtest_options_cpr`)
- Note: Not imported by live code (only research, admin use)

---

*Structure analysis: 2026-09-30*
