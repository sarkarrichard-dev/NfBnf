# Technology Stack

**Analysis Date:** 2026-09-30

## Languages

**Primary:**
- Python 3.11+ - Backend API, trading engine, strategies, ML (`index_ai/`, `crypto/`, `commodities/`)
- TypeScript 6.0.2 - Frontend dashboard source (`dashboard/src/`)
- JavaScript/Node.js 22 - Dashboard build/dev tooling

**Secondary:**
- Pine Script v6 - Strategy specifications (reference only, `crypto/strategies/*.pine`)

## Runtime

**Environment:**
- Python 3.12 (production Dockerfile, `FROM python:3.12-slim`)
- Python 3.11+ (local dev requirement in `pyproject.toml`)
- Node.js 22 (dashboard build, `FROM node:22-slim` in Dockerfile)

**Package Manager:**
- pip (Python)
- npm (Node.js)
- Lockfile: `package-lock.json` present (pinned Node dependencies)

## Frameworks

**Core (Backend):**
- FastAPI 0.115+ - REST API framework (`index_ai/server.py`, ~2125 lines)
- Uvicorn 0.30+ - ASGI server (production runner)

**Frontend:**
- React 19.2.6 - UI library (`dashboard/src/`)
- Vite 8.0.12 - Build tool and dev server (port 5173)
- TypeScript - Type checking (build step: `tsc -b && vite build`)

**Styling:**
- Tailwind CSS 4.3.0 - Utility-first CSS
- PostCSS 8.5.15 - CSS transformations

**Data/Analysis:**
- Pandas 2.2+ - Time series and data manipulation
- NumPy 1.26+ - Numerical computing
- Scikit-learn 1.4+ - ML utilities (preprocessing, scalers)

**Technical Analysis & Backtesting:**
- TA-Lib 0.8+ - Technical indicators (EMA, RSI, Supertrend, etc.)
- Backtrader 1.9+ - Backtesting framework (research/strategy validation)
- QuantLib 1.43+ - Options pricing (Black-Scholes proxy for backtests)
- Mibian 0.1 - Binomial tree option pricing

**Machine Learning:**
- Scikit-learn 1.4+ - ML models (dimensionality reduction, clustering)
- Joblib 1.3+ - Model serialization and caching
- Hugging Face Hub 0.23+ - Model downloading (`index_ai/hf_learning.py`)
- Transformers 4.40+ (optional `hf-local`) - FinBERT NLP (offline)
- Torch 2.2+ (optional `hf-local`) - Deep learning backend for transformers

**Dev/Testing:**
- Pytest 8+ - Test runner (660 tests, ~7-8 min)
- Ruff 0.6+ - Linter and formatter (target Python 3.11, line-length 100)

**Visualization:**
- Matplotlib 3.9+ - Charting (heatmaps, daily reports)

## Key Dependencies

**Critical (Infrastructure & Integrations):**
- httpx 0.27+ - Async HTTP client (Dhan API, Delta API, Telegram, HuggingFace, NSE)
- python-dotenv 1.0+ - Environment variable loading from `.env`
- Pydantic 2.7+ - Data validation and settings management (`index_ai/config.py`)

**Optional (Feature-specific):**
- boto3 1.34+ (install with `pip install -e ".[s3]"`) - AWS S3 backup of `memory/` after close
- websockets - Dhan live tick feed over WebSocket (streaming market data)

## Configuration

**Environment:**
- `.env` file (gitignored, permission-blocked in development)
- Loaded via `python-dotenv` at startup
- Fallback to defaults if unset (e.g., TRADING_MODE defaults to PAPER)
- Hot-reload on write via `index_ai.config.update_env_values()`

**Build:**
- `pyproject.toml` - Python project metadata, dependencies, dev tools
- `dashboard/tsconfig.json`, `dashboard/tsconfig.app.json`, `dashboard/tsconfig.node.json` - TypeScript compilation
- `dashboard/vite.config.ts` - Frontend build config
- `dashboard/eslint.config.js` - Linting config
- `Dockerfile` - Multi-stage: Node build for dashboard, Python runtime for API

## Deployment

**Production Build:**
```dockerfile
# Stage 1: Build dashboard (Node 22)
FROM node:22-slim → npm ci → npm run build → /build/dist

# Stage 2: Runtime (Python 3.12)
FROM python:3.12-slim
COPY dashboard/dist → /app/dashboard/dist
pip install . tzdata boto3
EXPOSE 8000
CMD ["uvicorn", "index_ai.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Runtime Mounts (Docker):**
- `.env` - bind-mount (settings write-through)
- `memory/` - volume (journals, models, caches)

**Starting Server (Local):**
```bash
python -m uvicorn index_ai.server:app --port 8000
```

**Dashboard Rebuild (After code changes):**
```bash
npm --prefix dashboard run build
```

## Platform Requirements

**Development:**
- Windows PowerShell (primary environment, Richard's setup)
- Python 3.11+ installed
- Node.js 22+ installed
- Git (repo management)

**Production:**
- Docker (containerized) or Linux/EC2 instance with Python 3.12
- AWS S3 bucket (optional, for `memory/` backup)
- Elastic IP (cloud deployment, for broker IP whitelisting)

## Linting & Code Quality

**Linter:** Ruff 0.6+
- Config: `pyproject.toml [tool.ruff]`
- Target: Python 3.11
- Line length: 100 characters
- Excludes: `reference/` (vendored submodule)

**Type Checking:**
```bash
npx tsc --noEmit  # Dashboard TypeScript validation
```

## Python Version Constraints

- **Local dev:** Python 3.11+ (stated in `pyproject.toml`)
- **Production:** Python 3.12-slim (Dockerfile)
- **Rationale:** 3.12 has faster startup; 3.11+ required for walrus operator and zoneinfo

## Timezone & Localization

- **App timezone:** Asia/Kolkata (IST)
- **Environment variable:** `TZ=Asia/Kolkata` (set in Dockerfile, `market_clock.py` uses `zoneinfo.ZoneInfo("Asia/Kolkata")`)
- **Timestamps:** Stored in IST, displayed in IST

---

*Stack analysis: 2026-09-30*
