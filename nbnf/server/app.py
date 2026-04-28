from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from nbnf.dhan.config import dhan_readiness
from nbnf.dhan.market_feed import market_feed_status
from nbnf.india.constituents import get_indices_catalog
from nbnf.ml.ingest import scan_and_ingest
from nbnf.options.local_chain import available_trade_dates, local_option_chain_heatmap
from nbnf.research.readiness import bot_readiness_snapshot
from nbnf.server import db
from nbnf.server.paths import STATIC_DIR
from nbnf.server.research import run_symbol_backtest
from nbnf.server.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="NfBnf", version="0.1.0", lifespan=lifespan)

app.include_router(ws_router)

static_path = Path(STATIC_DIR)
_favicon_svg = static_path / "favicon.svg"


@app.get("/api/india/indices-catalog", include_in_schema=False)
async def api_indices_catalog() -> dict:
    """Categorized index constituents for the multiselect watchlist picker."""
    return get_indices_catalog()


@app.get("/api/ml/datasets", include_in_schema=False)
async def api_ml_datasets() -> dict:
    """Last-ingested tabular dataset profiles (from local folders)."""
    return {
        "count": db.ml_datasets_count(),
        "summary": db.ml_datasets_summary(),
        "datasets": db.fetch_ml_datasets(limit=500),
    }


@app.post("/api/ml/datasets/ingest", include_in_schema=False)
async def api_ml_datasets_ingest() -> dict:
    """Re-scan ``data/``, ``data for ml/``, and ``NBNF_ML_EXTRA_DIRS`` into SQLite."""
    return scan_and_ingest()


@app.get("/api/research/backtest", include_in_schema=False)
async def api_research_backtest(symbol: str = "RELIANCE.NS", period: str = "5y", horizon: int = 5) -> dict:
    """Research-only walk-forward backtest of the current structural signal logic."""
    return run_symbol_backtest(symbol, period=period, horizon_bars=horizon)


@app.get("/api/bot/readiness", include_in_schema=False)
async def api_bot_readiness() -> dict:
    """Hard gate: explain why this build is research-only until live safeguards exist."""
    return bot_readiness_snapshot()


@app.get("/api/options/dates", include_in_schema=False)
async def api_options_dates(underlying: str = "nifty") -> dict:
    """Available local option-chain trade dates."""
    return {"underlying": underlying.upper(), "dates": available_trade_dates(underlying)}


@app.get("/api/options/heatmap", include_in_schema=False)
async def api_options_heatmap(underlying: str = "nifty", trade_date: str | None = None) -> dict:
    """Local option-chain heatmap from historical CSVs."""
    return local_option_chain_heatmap(underlying, trade_date=trade_date)


@app.get("/api/dhan/readiness", include_in_schema=False)
async def api_dhan_readiness() -> dict:
    """Dhan data-feed readiness without exposing secrets."""
    return dhan_readiness()


@app.get("/api/dhan/feed/status", include_in_schema=False)
async def api_dhan_feed_status() -> dict:
    """Dhan live-feed capabilities and configured state."""
    return market_feed_status()


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> RedirectResponse:
    """Browsers request /favicon.ico by default; avoid 404 noise in logs."""
    if _favicon_svg.is_file():
        return RedirectResponse(url="/favicon.svg", status_code=307)
    return RedirectResponse(url="/", status_code=302)


if static_path.is_dir():
    app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")
