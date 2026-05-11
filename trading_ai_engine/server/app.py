from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import Body, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from trading_ai_engine.dhan.config import dhan_readiness
from trading_ai_engine.dhan.market_feed import market_feed_status
from trading_ai_engine.india.constituents import get_indices_catalog
from trading_ai_engine.ml.hf_online_digest import (
    build_hf_online_learning_digest,
    online_learning_status,
)
from trading_ai_engine.ml.ingest import scan_and_ingest
from trading_ai_engine.ml.market_learn import (
    InternetDatasetConfig,
    build_market_training_frame,
    data_quality_report,
    download_indian_market_history,
    learning_status,
    train_market_model,
)
from trading_ai_engine.options.local_chain import available_trade_dates, local_option_chain_heatmap
from trading_ai_engine.research.readiness import bot_readiness_snapshot
from trading_ai_engine.server import db
from trading_ai_engine.server.paths import DASHBOARD_DIR
from trading_ai_engine.server.research import run_symbol_backtest
from trading_ai_engine.server.ws import router as ws_router
from trading_ai_engine.trading.evolution import evolution_snapshot
from trading_ai_engine.trading.paper import place_paper_order, recent_paper_orders
from trading_ai_engine.trading.readiness import workstation_readiness
from trading_ai_engine.trading.risk import load_risk_config
from trading_ai_engine.server import analyze
from trading_ai_engine.learning.post_mortem import run_post_mortem
from trading_ai_engine.learning.refinement import learning_loop_status, load_refinement_for_context
from trading_ai_engine.market_vision.providers import HeatmapSource, fetch_heatmap_snapshot
from trading_ai_engine.quant.backtest_sweep import sweep_backtest_grid
from trading_ai_engine.quant.learnable_parameters import LEARNABLE_PARAMETER_CATALOG
from trading_ai_engine.quant.strategy_taxonomy import GROWW_STRATEGY_TAXONOMY


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Trading AI Workstation", version="0.1.0", lifespan=lifespan)

app.include_router(ws_router)

dashboard_path = Path(DASHBOARD_DIR)
_favicon_svg = dashboard_path / "favicon.svg"


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
    """Re-scan local data folders into SQLite (optional catalog; brain learning defaults to online Hub + Yahoo)."""
    return scan_and_ingest()


@app.get("/api/ml/online-learning/status", include_in_schema=False)
async def api_ml_online_learning_status() -> dict:
    """Hugging Face Hub streaming config for brain digests (no local uploads required)."""
    return online_learning_status()


@app.get("/api/ml/online-learning/preview", include_in_schema=False)
async def api_ml_online_learning_preview(max_rows: int = 8) -> dict:
    """Pull a short streaming sample from TRADING_AI_HF_LEARNING_DATASETS (for operator verification)."""
    text, meta = build_hf_online_learning_digest(max_rows_per_dataset=max(3, min(max_rows, 40)))
    return {"meta": meta, "digest_preview": (text or "")[:4000]}


@app.get("/api/ml/market-learning/status", include_in_schema=False)
async def api_market_learning_status() -> dict:
    """Downloaded Indian market internet dataset and trained model status."""
    return learning_status()


@app.get("/api/ml/market-learning/quality", include_in_schema=False)
async def api_market_learning_quality() -> dict:
    """Pre-training data sanity checks on manifest and supervised training frame."""
    return data_quality_report()


@app.post("/api/ml/market-learning/train", include_in_schema=False)
async def api_market_learning_train() -> dict:
    """Train the local market model from the downloaded supervised frame."""
    return train_market_model()


@app.post("/api/ml/market-learning/download", include_in_schema=False)
async def api_market_learning_download(years: int = 7, max_symbols: int = 25) -> dict:
    """Download Indian daily market candles and build the supervised learning frame."""
    cfg = InternetDatasetConfig(years=years, period=f"{years}y", max_symbols=max_symbols)
    download = download_indian_market_history(config=cfg)
    frame = build_market_training_frame(cfg)
    return {"download": download, "frame": frame, "status": learning_status()}


@app.post("/api/brain/analyze", include_in_schema=False)
async def api_brain_analyze(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Run the brain for one symbol and return the paper trade plan."""
    symbol = str(payload.get("symbol") or "RELIANCE.NS").strip()
    period = str(payload.get("period") or "1y")
    interval = str(payload.get("interval") or "1d")
    market_focus = str(payload.get("market_focus") or os.environ.get("TRADING_AI_MARKET_FOCUS") or "balanced")
    return analyze.run_analyze(
        symbol,
        period,
        use_llm=bool(payload.get("use_llm", False)),
        include_yahoo_deep=bool(payload.get("include_yahoo_deep", False)),
        include_ml_digest=bool(payload.get("include_ml_digest", False)),
        include_heatmap=bool(payload.get("include_heatmap", False)),
        heatmap_underlying=str(payload.get("heatmap_underlying") or "nifty"),
        heatmap_source=str(payload.get("heatmap_source") or "auto"),
        interval=interval,
        market_focus=market_focus,
        include_global_context=bool(payload.get("include_global_context", True)),
        include_hf_online_digest=bool(payload.get("include_hf_online_digest", True)),
        include_strategy_features=bool(payload.get("include_strategy_features", True)),
    )


@app.get("/api/market/heatmap", include_in_schema=False)
async def api_market_heatmap(
    underlying: str = "nifty",
    trade_date: str | None = None,
    source: str = "auto",
) -> dict:
    """Unified heatmap snapshot (local CSV now; Dhan when wired)."""
    src = source.strip().lower()
    hs: HeatmapSource = cast(HeatmapSource, src if src in ("auto", "local", "dhan") else "auto")
    return fetch_heatmap_snapshot(underlying, trade_date=trade_date, source=hs)


@app.post("/api/learning/post-mortem", include_in_schema=False)
async def api_learning_post_mortem(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Compare a past finding to forward returns; feeds the self-learning refinement loop."""
    return run_post_mortem(
        str(payload.get("finding_id") or ""),
        horizon_bars=int(payload.get("horizon_bars") or 5),
    )


@app.get("/api/learning/loops", include_in_schema=False)
async def api_learning_loops() -> dict:
    """Self-learning file state + next-pass refinement nudge + recent post-mortems."""
    events = db.fetch_evolution_events(limit=60)
    pm = [e for e in events if e.get("event_type") == "post_mortem"]
    return {
        "refinement_for_next_brain": load_refinement_for_context(),
        "loop_file": learning_loop_status(),
        "recent_post_mortems": pm[:15],
    }


@app.get("/api/research/backtest", include_in_schema=False)
async def api_research_backtest(
    symbol: str = "RELIANCE.NS",
    period: str = "5y",
    interval: str = "1d",
    horizon: int = 5,
    cost_bps: float = 8.0,
    signal_mode: str = "structural",
    fast_ma: int = 20,
    slow_ma: int = 50,
    z_lookback: int = 20,
    z_entry: float = 1.0,
) -> dict:
    """Research-only walk-forward backtest: structural brain score, trend MA crossover, or mean-reversion z."""
    return run_symbol_backtest(
        symbol,
        period=period,
        interval=interval,
        horizon_bars=horizon,
        cost_bps=cost_bps,
        signal_mode=signal_mode,
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        z_lookback=z_lookback,
        z_entry=z_entry,
    )


@app.get("/api/quant/strategy-taxonomy", include_in_schema=False)
async def api_quant_strategy_taxonomy() -> dict:
    """Groww-style strategy classes mapped to this workstation (educational; see reference_url)."""
    return GROWW_STRATEGY_TAXONOMY


@app.get("/api/quant/parameter-catalog", include_in_schema=False)
async def api_quant_parameter_catalog() -> dict:
    """Maps classic quant learnable families to this repo's env vars, modules, and HTTP knobs."""
    return LEARNABLE_PARAMETER_CATALOG


@app.get("/api/quant/backtest-sweep", include_in_schema=False)
async def api_quant_backtest_sweep(
    symbol: str = "RELIANCE.NS",
    period: str = "5y",
) -> dict:
    """Coarse grid over horizon × cost_bps (research only); ranks combinations by equity / PF / drawdown."""
    return sweep_backtest_grid(symbol, period=period)


@app.get("/api/bot/readiness", include_in_schema=False)
async def api_bot_readiness() -> dict:
    """Hard gate: explain why this build is research-only until live safeguards exist."""
    return bot_readiness_snapshot()


@app.get("/api/trading/risk", include_in_schema=False)
async def api_trading_risk() -> dict:
    """Risk configuration used by the paper-trading router."""
    return load_risk_config().to_dict()


@app.get("/api/trading/readiness", include_in_schema=False)
async def api_trading_readiness() -> dict:
    """Roadmap-aligned gates: kill switch, paper sessions, data-quality snapshot, catalog health."""
    return workstation_readiness()


@app.get("/api/trading/paper/orders", include_in_schema=False)
async def api_trading_paper_orders() -> dict:
    """Recent paper orders and aggregate paper exposure."""
    return recent_paper_orders()


@app.post("/api/trading/paper/order", include_in_schema=False)
async def api_trading_paper_order(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Place a risk-checked paper order from the current brain plan."""
    return place_paper_order(
        finding_id=str(payload.get("finding_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        plan=payload.get("plan") if isinstance(payload.get("plan"), dict) else {},
        brain=payload.get("brain") if isinstance(payload.get("brain"), dict) else {},
    )


@app.get("/api/trading/evolution", include_in_schema=False)
async def api_trading_evolution(symbol: str | None = None) -> dict:
    """Brain evolution events from feedback and paper execution."""
    return evolution_snapshot(symbol)


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


if dashboard_path.is_dir():
    app.mount("/", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
