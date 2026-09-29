# External Integrations

**Analysis Date:** 2026-09-30

## APIs & External Services

**Dhan Broker (NSE/BSE F&O - India Index Options & Futures):**
- API endpoints: `https://api.dhan.co/v2/` (configurable via `DHAN_API_BASE_URL`)
- Auth endpoints: `https://auth.dhan.co` (configurable via `DHAN_AUTH_BASE_URL`)
- SDK/Client: Custom HTTP client (`index_ai/dhan.py`, `index_ai/dhan_auth.py`) using httpx
- Rate limiting: ~3 req/s (internal limiter in `index_ai/dhan.py:_RateLimiter`, 0.65s min interval)
- Endpoints used:
  - `/orders` - Place/modify/cancel orders
  - `/positions` - Fetch open positions
  - `/orderbook` - Live order status
  - `/charts/intraday` - OHLC candles (up to 90-day windows)
  - Market feed & streaming - WebSocket for live ticks
- Auth: OAuth 2.0 + TOTP + JWT token refresh (`index_ai/dhan_auth.py:auto_refresh_dhan_token`)
- Credentials env vars: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `DHAN_API_KEY`, `DHAN_API_SECRET`, `DHAN_TOKEN_EXPIRY`

**Delta Exchange India (Crypto Perpetuals & Options):**
- API base URL: `https://api.india.delta.exchange` (hardcoded in `crypto/config.py`)
- SDK/Client: Custom signed HTTP client (`crypto/delta/client.py`) using httpx
- Signature scheme: HMAC-SHA256 of `METHOD + timestamp + path + query_string + body`
- Rate limiting: Backoff on 429; max wait 8 seconds (`_MAX_429_WAIT`)
- Endpoints used:
  - Order placement / modification / cancellation
  - Wallet & margin data
  - Products (symbols, specs) - `crypto/delta/products.py`
  - Market data (public feeds)
- Auth: API key + secret (HMAC signing per request)
- Credentials env vars: `DELTA_API_KEY`, `DELTA_API_SECRET` (loaded in `crypto/config.py`)
- IPv4 pin option: `CRYPTO_FORCE_IPV4` forces Delta traffic over IPv4 only

**NSE (India VIX):**
- Endpoint: `https://www.nseindia.com/api/allIndices`
- SDK/Client: Direct httpx call (`index_ai/market_context/vix.py`)
- Headers: User-Agent spoofing + Referer for scraping
- Data cached: `memory/vix.json` (refreshed ~hourly)
- Metrics: India VIX level, percentile, regime (CALM / NORMAL / ELEVATED / STRESSED)

**Telegram Bot API:**
- Endpoint: `https://api.telegram.org/bot{token}/sendMessage`
- SDK/Client: Direct httpx POST (`index_ai/notify.py`)
- Credentials env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Fire-and-forget: Daemon thread, failures swallowed
- Deduplication: Restart-safe stamps in `memory/.notify_seen.json` (6h window per message key)
- Message types: Trade opens/closes, daily reports, pre-open alerts (indices); crypto trade alerts, daily summaries
- No-op if env vars unset

**Hugging Face Inference API:**
- Endpoint: `https://api-inference.huggingface.co/models/{model_id}`
- SDK/Client: Direct httpx POST (`index_ai/hf_learning.py`)
- Model: FinBERT (default `ProsusAI/finbert`) for sentiment analysis on trade setups
- Credentials env var: `HF_TOKEN` or `HUGGINGFACE_TOKEN`
- Optional: Can be disabled by not setting token
- Offline mode: `pip install -e ".[hf-local]"` for local transformers + torch (large download)

## Data Storage

**Databases:**
- SQLite (local file)
  - `memory/trade_memory.sqlite` - Trade journal (OHLC, entries, exits, P&L, feedback)
  - `memory/market_log.sqlite` - Market observations (candles, ticks from exchange, participant OI)
  - WAL mode enabled (Write-Ahead Logging for concurrent access)
  - Online backup via sqlite3 `.backup()` API (used by S3 backup to snapshot live DBs)
  - Connection: `sqlite3.connect(DB_PATH, timeout=30)`
  - Client: Python stdlib `sqlite3`
- Connection pooling: None (simple per-request connections in most handlers)
- Migration: Explicit CREATE TABLE IF NOT EXISTS (no framework)

**File Storage:**
- Local filesystem only (git-ignored `memory/` directory)
  - `memory/trade_memory.sqlite` - Trade journal
  - `memory/market_log.sqlite` - Market log
  - `memory/models/` - Trained ML models (scikit-learn joblib format)
  - `memory/daily_reports/` - Daily JSON reports + CSV exports
  - `memory/market_context.json`, `memory/participant_oi.json`, `memory/vix.json` - Cached market metadata
  - `memory/crypto_journal.jsonl` - Crypto trade history (newline-delimited JSON)
  - `memory/archive/` - Archived old journals (for historical reference, not active)

**S3 Backup (Optional):**
- Bucket env var: `S3_BACKUP_BUCKET`
- Enable via: `ENABLE_S3_BACKUP=true`
- SDK: boto3 (installed with `pip install -e ".[s3]"`)
- Runs after market close (EOD) via `index_ai/daily_ops.run_eod()` or manual `python -m index_ai.cloud_backup`
- What's uploaded: SQLite DBs (online backup), trained models, daily reports, market-context JSONs, vix.json, spread_skips.json, participant_oi.json, ai_commentary.json, daily_ops.json, learned_settings.json
- What's excluded: Candle CSVs, datasets, logs (regenerable)
- Credentials: AWS CLI chain (`~/.aws/credentials` from `aws configure`, NOT from `.env`)
- Versioning: Relies on bucket versioning for point-in-time recovery

**Caching:**
- In-memory: Python `@lru_cache` decorators (model predictions, candle prices, OI levels)
- File-based: JSON caches (vix.json, market_context.json, participant_oi.json)
- No Redis or external cache

## Authentication & Identity

**Auth Providers:**
- **Dhan OAuth 2.0** - Consent flow + JWT access token refresh
  - Endpoints: `/auth/authorization/user` (consent), `/auth/token` (refresh)
  - TOTP support: Automated token generation via `index_ai/dhan_auth.py:generate_access_token_via_totp()`
  - Token auto-refresh: Background loop every N seconds (configurable)
  - No built-in user management (single `.env` per instance)

- **Delta HMAC-SHA256** - Request signing (no OAuth)
  - Every request signed with API secret
  - Timestamp embedded in signature (rejects if >5s stale)
  - No user identity; credential pair = account access

- **Telegram Bot Token** - Straight API key in `sendMessage` URL
  - No OAuth, just bearer token

- **Dashboard Password (optional, 2026-09-20):**
  - Env var: `DASHBOARD_PASSWORD`
  - Gate: HTTP Basic Auth (any username + password required)
  - Affects: All endpoints except `/api/health`
  - Enforced in: `index_ai/admin_auth.py:require_admin_secret` (5 endpoints that arm live orders)
  - Lock-out: 10 wrong attempts = 15-min IP ban
  - Default: Off (no-op if unset)

- **No per-user auth** - Roadmap for multi-tenant cloud, currently single-user only

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Rollbar, or external service)
- Local logging: Python stdlib `logging` → console + optional file
- HTTP logger suppression: `index_ai.server._quiet_http_loggers()` sets httpx/httpcore to WARNING (avoids token leaks in logs)

**Logs:**
- Stdout only (no file rotation configured)
- Log level: INFO default
- Sensitive data: Never logged (tokens, credentials scrubbed; Telegram bot token was previously leaked but fixed 2026-09-14)

**Health Checks:**
- Endpoint: `GET /api/health` (stays open, no auth)
- Docker healthcheck: `curl http://127.0.0.1:8000/api/health` every 30s (timeout 5s, start-period 60s)

**Performance Monitoring:**
- None (no APM service)
- Manual tracking: Memory/CPU usage observed locally on Windows PC
- Dhan rate-limiter: Logged on exhaustion

## CI/CD & Deployment

**Hosting:**
- Currently: Windows PC (Richard's local machine)
- Target: AWS EC2 (personal instance first, then multi-tenant containers per subscriber)
- Platform: Docker containerized (`Dockerfile` for cloud, bash script for local dev)

**CI Pipeline:**
- None deployed yet (no GitHub Actions or Jenkins)
- Pre-commit: Ruff linter can be wired as a hook
- Tests: Manual `pytest -q` (660 tests, ~7-8 min)

**Database Migrations:**
- Manual: `init_db()` creates schema on first connection (CREATE TABLE IF NOT EXISTS)
- No migration framework (Alembic, etc.)

**Secrets Management:**
- Currently: `.env` file (gitignored, permission-blocked)
- Cloud roadmap: AWS Secrets Manager (or field-level encryption in Postgres once multi-tenant)
- Not using: HashiCorp Vault, Bitwarden Server, or other vault products

## Environment Configuration

**Required env vars (must be set):**
- `DHAN_CLIENT_ID` - Dhan OAuth client identifier
- `DHAN_API_KEY` - Dhan app API key (for generating consent tokens)
- `DHAN_API_SECRET` - Dhan app API secret
- `DELTA_API_KEY` - Delta Exchange API key (if crypto lane enabled)
- `DELTA_API_SECRET` - Delta Exchange API secret (if crypto lane enabled)

**Optional env vars (sensible defaults if unset):**
- `DHAN_ACCESS_TOKEN` - Dhan JWT access token (auto-refreshes if expired)
- `DHAN_TOKEN_EXPIRY` - Token expiry timestamp (used for refresh logic)
- `DHAN_API_BASE_URL` - Default: `https://api.dhan.co/v2`
- `DHAN_AUTH_BASE_URL` - Default: `https://auth.dhan.co`
- `TRADING_MODE` - `PAPER` or `LIVE` (default PAPER; live orders require ALLOW_LIVE_TRADING=true too)
- `ALLOW_LIVE_TRADING` - Must be `true` to arm live orders (two-factor gate with TRADING_MODE)
- `TELEGRAM_BOT_TOKEN` - Telegram bot token (alerts disabled if unset)
- `TELEGRAM_CHAT_ID` - Telegram chat ID for alerts (alerts disabled if unset)
- `ENABLE_S3_BACKUP` - `true` to enable S3 backup
- `S3_BACKUP_BUCKET` - S3 bucket name (required if S3 backup enabled)
- `HF_TOKEN` / `HUGGINGFACE_TOKEN` - HuggingFace API token for sentiment analysis
- `PUBLIC_DEPLOY` - Set to `true` in cloud mode (gates password requirement)
- `DASHBOARD_PASSWORD` - Required password for admin endpoints if PUBLIC_DEPLOY=true
- `ENABLE_TICK_FEED` - `true` to enable Dhan WebSocket tick feed (records to market_log.sqlite)
- `CANDLE_INTERVAL_MINUTES` - Backtest/live chart interval (1, 5, 15, 25, 60; default 1)

**Config file:** None (all env-based)

## Webhooks & Callbacks

**Incoming Webhooks:**
- None (no inbound event handlers for external services)

**Outgoing Notifications:**
- Telegram: `https://api.telegram.org/bot{token}/sendMessage` (fire-and-forget POST)
  - Triggered on: trade open, trade close, daily report, pre-open alert
- Email: None

**Broker Order Callbacks:**
- Synchronous polling: Regular API calls to fetch order status (no webhook from Dhan)
- Dhan market feed: WebSocket stream for live ticks (if `ENABLE_TICK_FEED=true`)

## Third-Party Integrations (Reference/Research Only)

**OpenBB (Investing Section):**
- Standalone isolated venv: `.venv-openbb` (not in main `pyproject.toml`)
- Bridge: `investing/openbb_bridge.py` (subprocess calls)
- Purpose: Stock screener / fundamental data (parked, not actively wired to live trading)

**Reference/Openalgo Submodule:**
- Git submodule: `reference/openalgo/` (read-only)
- Contains: Dhan websocket packet layouts, telegram bot reference implementations, auth examples
- Used for: Cross-reference on Dhan protocol details (do not copy; check it before implementing)

---

*Integration audit: 2026-09-30*
