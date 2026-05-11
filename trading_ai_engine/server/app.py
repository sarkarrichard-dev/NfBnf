from __future__ import annotations

import csv
import io
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from trading_ai_engine.dhan.config import dhan_readiness
from trading_ai_engine.dhan.market_feed import market_feed_status
from trading_ai_engine.dhan.quote_client import dhan_quote_operator_status
from trading_ai_engine.india.constituents import get_indices_catalog
from trading_ai_engine.india.ist_time import format_utc_iso_in_ist, ist_date_window_to_utc_bounds
from trading_ai_engine.india.nse_yahoo import normalize_nse_yahoo_symbol, require_nifty_option_underlying
from trading_ai_engine.ml.hf_online_digest import (
    build_hf_online_learning_digest,
    online_learning_status,
)
from trading_ai_engine.ml.ingest import scan_and_ingest
from trading_ai_engine.ml.market_learn import (
    InternetDatasetConfig,
    build_market_training_frame,
    data_quality_report,
    download_indian_intraday_5m,
    download_indian_market_history,
    learning_status,
    train_market_model,
)
from trading_ai_engine.ml.pattern_feedback import refresh_pattern_live_overlay
from trading_ai_engine.options.local_chain import available_trade_dates, local_option_chain_heatmap
from trading_ai_engine.research.readiness import bot_readiness_snapshot
from trading_ai_engine.server import db
from trading_ai_engine.server.paths import DASHBOARD_DIR
from trading_ai_engine.server.research import run_symbol_backtest
from trading_ai_engine.server.ws import router as ws_router
from trading_ai_engine.trading.evolution import evolution_snapshot
from trading_ai_engine.trading.paper import close_paper_order, place_paper_order, recent_paper_orders
from trading_ai_engine.trading.readiness import workstation_readiness
from trading_ai_engine.trading.risk import load_risk_config
from trading_ai_engine.server import analyze
from trading_ai_engine.learning.post_mortem import run_post_mortem
from trading_ai_engine.learning.refinement import learning_loop_status, load_refinement_for_context
from trading_ai_engine.market_vision.providers import HeatmapSource, fetch_heatmap_snapshot
from trading_ai_engine.quant.backtest_sweep import sweep_backtest_grid
from trading_ai_engine.quant.learnable_parameters import LEARNABLE_PARAMETER_CATALOG
from trading_ai_engine.quant.strategy_taxonomy import GROWW_STRATEGY_TAXONOMY
from trading_ai_engine.research.eval_harness import run_mode_comparison


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    api_paths = sorted(
        {
            str(getattr(route, "path", "") or "")
            for route in app.routes
            if getattr(route, "path", None) and str(route.path).startswith("/api")
        }
    )
    app.state.api_paths = api_paths
    print(
        f"[Trading AI Workstation] {len(api_paths)} /api routes loaded from {Path(__file__).resolve()}",
        flush=True,
    )
    yield


app = FastAPI(title="Trading AI Workstation", version="0.1.0", lifespan=lifespan)

app.include_router(ws_router)

dashboard_path = Path(DASHBOARD_DIR)
_favicon_svg = dashboard_path / "favicon.svg"


@app.get("/api/health", include_in_schema=False)
async def api_health() -> dict[str, Any]:
    """
    If this returns 404, another program is bound to the port (or a very old build).

    Includes the resolved ``app.py`` path and every registered ``/api`` route path so you can
    confirm the running interpreter loaded this repo.
    """
    paths: list[str] = list(getattr(app.state, "api_paths", None) or [])
    if not paths:
        paths = sorted(
            {
                str(getattr(route, "path", "") or "")
                for route in app.routes
                if getattr(route, "path", None) and str(route.path).startswith("/api")
            }
        )
    mod_file = str(Path(__file__).resolve())
    critical = (
        "/api/ml/online-learning/status",
        "/api/ml/online-learning/preview",
        "/api/research/eval-modes",
        "/api/quant/strategy-taxonomy",
    )
    pset = set(paths)
    return {
        "ok": True,
        "service": "trading_ai_workstation",
        "app_module_file": mod_file,
        "python_executable": sys.executable,
        "api_route_count": len(paths),
        "api_paths": paths,
        "critical_routes_present": {p: p in pset for p in critical},
    }


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


@app.post("/api/ml/market-learning/download-intraday", include_in_schema=False)
async def api_market_learning_download_intraday(max_symbols: int = 25, period: str = "60d") -> dict:
    """Download 5m Yahoo bars (recent window) and rebuild the supervised frame with 5m–3h candles."""
    ms = max(5, min(int(max_symbols), 80))
    cfg = InternetDatasetConfig(
        max_symbols=ms,
        intraday_symbols_cap=ms,
        intraday_period=str(period or "60d").strip() or "60d",
    )
    intra = download_indian_intraday_5m(config=cfg)
    frame = build_market_training_frame(cfg)
    return {"intraday_download": intra, "frame": frame, "status": learning_status()}


@app.post("/api/ml/pattern-feedback/refresh", include_in_schema=False)
async def api_pattern_feedback_refresh() -> dict:
    """Merge closed paper trades (good/bad) into ``pattern_live_overlay`` on the market model JSON."""
    return refresh_pattern_live_overlay()


@app.post("/api/brain/analyze", include_in_schema=False)
async def api_brain_analyze(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Run the brain for one symbol and return the paper trade plan."""
    symbol = str(payload.get("symbol") or "^NSEI").strip()
    period = str(payload.get("period") or "1y")
    interval = str(payload.get("interval") or "1d")
    market_focus = str(payload.get("market_focus") or os.environ.get("TRADING_AI_MARKET_FOCUS") or "balanced")
    try:
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
            include_dhan_snapshot=bool(payload.get("include_dhan_snapshot", True)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/market/heatmap", include_in_schema=False)
async def api_market_heatmap(
    underlying: str = "nifty",
    trade_date: str | None = None,
    source: str = "auto",
) -> dict:
    """Unified heatmap snapshot (local CSV now; Dhan when wired)."""
    try:
        u = require_nifty_option_underlying(underlying)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    src = source.strip().lower()
    hs: HeatmapSource = cast(HeatmapSource, src if src in ("auto", "local", "dhan") else "auto")
    return fetch_heatmap_snapshot(u, trade_date=trade_date, source=hs)


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


@app.get("/api/research/eval-modes", include_in_schema=False)
async def api_research_eval_modes(
    symbol: str = "^NSEI",
    period: str = "2y",
    interval: str = "1d",
    horizon: int = 5,
    cost_bps: float = 12.0,
    spread_bps: float = 0.0,
) -> dict:
    """Compare structural vs trend_ma vs mean_reversion_z on one symbol (research only)."""
    try:
        symbol = normalize_nse_yahoo_symbol(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    rows = run_mode_comparison(
        symbol,
        period=period,
        interval=interval,
        horizon_bars=horizon,
        cost_bps=cost_bps,
        spread_bps=spread_bps,
    )
    return {"symbol": symbol, "period": period, "interval": interval, "rows": rows}


@app.get("/api/research/backtest", include_in_schema=False)
async def api_research_backtest(
    symbol: str = "RELIANCE.NS",
    period: str = "5y",
    interval: str = "1d",
    horizon: int = 5,
    cost_bps: float = 8.0,
    spread_bps: float = 0.0,
    signal_mode: str = "structural",
    fast_ma: int = 20,
    slow_ma: int = 50,
    z_lookback: int = 20,
    z_entry: float = 1.0,
) -> dict:
    """Research-only walk-forward backtest: structural brain score, trend MA crossover, or mean-reversion z."""
    try:
        symbol = normalize_nse_yahoo_symbol(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return run_symbol_backtest(
        symbol,
        period=period,
        interval=interval,
        horizon_bars=horizon,
        cost_bps=cost_bps,
        spread_bps=spread_bps,
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
    try:
        symbol = normalize_nse_yahoo_symbol(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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


@app.get("/api/ml/findings/recent", include_in_schema=False)
async def api_ml_findings_recent(limit: int = 40) -> dict:
    """Recent brain findings stored in SQLite (symbol, summary, tags, metrics)."""
    lim = max(1, min(int(limit or 40), 200))
    return {"findings": db.fetch_findings_recent(limit=lim)}


@app.get("/api/trading/paper/history", include_in_schema=False)
async def api_trading_paper_history(
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 500,
) -> dict:
    """Paper orders in a window; ``YYYY-MM-DD`` bounds are **IST calendar days** (mapped to UTC for ``created_at``)."""
    af, bt = ist_date_window_to_utc_bounds(date_from, date_to)
    lim = max(1, min(int(limit or 500), 5000))
    orders = db.fetch_paper_orders_in_range(created_after=af, created_before=bt, limit=lim)
    summary = db.paper_trading_summary_in_range(created_after=af, created_before=bt)
    return {
        "orders": orders,
        "summary": summary,
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "timezone": "Asia/Kolkata",
            "interpretation": "Plain dates are inclusive IST calendar days; stored timestamps remain UTC.",
        },
    }


@app.get("/api/trading/paper/pnl-summary", include_in_schema=False)
async def api_trading_paper_pnl_summary(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Window summary: exposure plus realized PnL from paper rows closed via ``POST /api/trading/paper/close``."""
    af, bt = ist_date_window_to_utc_bounds(date_from, date_to)
    exposure = db.paper_trading_summary_in_range(created_after=af, created_before=bt)
    return {
        "window": {
            "date_from": date_from,
            "date_to": date_to,
            "timezone": "Asia/Kolkata",
            "interpretation": "Plain dates are inclusive IST calendar days.",
        },
        "exposure": exposure,
        "realized_pnl_total": exposure.get("realized_pnl_total"),
        "closed_orders": exposure.get("closed_orders"),
        "open_filled_orders": exposure.get("open_filled_orders"),
        "disclaimer": (
            "Realized PnL sums rows with status closed_paper (exit_price + realized_pnl). "
            "Omit exit_price on close to use Yahoo last daily close as a mark (best-effort)."
        ),
    }


@app.get("/api/trading/paper/export.csv", include_in_schema=False)
async def api_trading_paper_export_csv(date_from: str | None = None, date_to: str | None = None) -> Response:
    """Download paper orders as CSV for the selected window (IST day bounds when ``YYYY-MM-DD``)."""
    af, bt = ist_date_window_to_utc_bounds(date_from, date_to)
    orders = db.fetch_paper_orders_in_range(created_after=af, created_before=bt, limit=8000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "created_at_utc",
            "created_at_ist",
            "symbol",
            "side",
            "quantity",
            "entry_price",
            "stop_loss",
            "target",
            "notional",
            "risk_amount",
            "status",
            "finding_id",
            "model_version",
            "exit_price",
            "exit_at_utc",
            "exit_at_ist",
            "realized_pnl",
        ]
    )
    for o in orders:
        cat = str(o.get("created_at") or "")
        try:
            cist = format_utc_iso_in_ist(cat) if cat else ""
        except Exception:
            cist = ""
        eat = str(o.get("exit_at") or "")
        try:
            eist = format_utc_iso_in_ist(eat) if eat else ""
        except Exception:
            eist = ""
        w.writerow(
            [
                o.get("id"),
                cat,
                cist,
                o.get("symbol"),
                o.get("side"),
                o.get("quantity"),
                o.get("entry_price"),
                o.get("stop_loss"),
                o.get("target"),
                o.get("notional"),
                o.get("risk_amount"),
                o.get("status"),
                o.get("finding_id"),
                o.get("model_version"),
                o.get("exit_price"),
                eat,
                eist,
                o.get("realized_pnl"),
            ]
        )
    safe_from = (date_from or "all").replace(":", "-")
    safe_to = (date_to or "all").replace(":", "-")
    fn = f"paper_orders_{safe_from}_{safe_to}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )


@app.post("/api/trading/paper/order", include_in_schema=False)
async def api_trading_paper_order(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Place a risk-checked paper order from the current brain plan."""
    try:
        return place_paper_order(
            finding_id=str(payload.get("finding_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            plan=payload.get("plan") if isinstance(payload.get("plan"), dict) else {},
            brain=payload.get("brain") if isinstance(payload.get("brain"), dict) else {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/trading/paper/close", include_in_schema=False)
async def api_trading_paper_close(payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    """Close a paper row at an explicit exit price, or at Yahoo last daily close when ``exit_price`` is omitted."""
    out = close_paper_order(order_id=str(payload.get("order_id") or ""), exit_price=payload.get("exit_price"))
    if out.get("status") == "error":
        raise HTTPException(status_code=400, detail=str(out.get("reason") or "close_failed"))
    return out


@app.get("/api/trading/evolution", include_in_schema=False)
async def api_trading_evolution(symbol: str | None = None) -> dict:
    """Brain evolution events from feedback and paper execution."""
    if symbol:
        try:
            symbol = normalize_nse_yahoo_symbol(symbol)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return evolution_snapshot(symbol)


@app.get("/api/options/dates", include_in_schema=False)
async def api_options_dates(underlying: str = "nifty") -> dict:
    """Available local option-chain trade dates."""
    try:
        u = require_nifty_option_underlying(underlying)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"underlying": u.upper(), "dates": available_trade_dates(u)}


@app.get("/api/options/heatmap", include_in_schema=False)
async def api_options_heatmap(underlying: str = "nifty", trade_date: str | None = None) -> dict:
    """Local option-chain heatmap from historical CSVs."""
    try:
        u = require_nifty_option_underlying(underlying)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return local_option_chain_heatmap(u, trade_date=trade_date)


@app.get("/api/dhan/readiness", include_in_schema=False)
async def api_dhan_readiness() -> dict:
    """Dhan data-feed readiness without exposing secrets."""
    return dhan_readiness()


@app.get("/api/dhan/feed/status", include_in_schema=False)
async def api_dhan_feed_status() -> dict:
    """Dhan live-feed capabilities and configured state."""
    return market_feed_status()


@app.get("/api/dhan/quote-map", include_in_schema=False)
async def api_dhan_quote_map() -> dict:
    """Operator view: which symbols have Dhan LTP security_id mappings (see TRADING_AI_DHAN_LTP_MAP)."""
    return dhan_quote_operator_status()


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> RedirectResponse:
    """Browsers request /favicon.ico by default; avoid 404 noise in logs."""
    if _favicon_svg.is_file():
        return RedirectResponse(url="/favicon.svg", status_code=307)
    return RedirectResponse(url="/", status_code=302)


# Serve the dashboard with explicit routes (no catch-all ``Mount("/")``).
# A root StaticFiles mount can swallow ``/api/...`` on some Windows / ASGI stacks.
_DASH_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

if dashboard_path.is_dir():

    @app.get("/", include_in_schema=False)
    async def dashboard_index() -> FileResponse:
        return FileResponse(
            dashboard_path / "index.html",
            media_type="text/html",
            headers=dict(_DASH_NO_CACHE),
        )

    @app.get("/index.html", include_in_schema=False)
    async def dashboard_index_alias() -> FileResponse:
        return FileResponse(
            dashboard_path / "index.html",
            media_type="text/html",
            headers=dict(_DASH_NO_CACHE),
        )

    @app.get("/app.js", include_in_schema=False)
    async def dashboard_app_js() -> FileResponse:
        return FileResponse(
            dashboard_path / "app.js",
            media_type="application/javascript",
            headers=dict(_DASH_NO_CACHE),
        )

    @app.get("/styles.css", include_in_schema=False)
    async def dashboard_styles_css() -> FileResponse:
        return FileResponse(
            dashboard_path / "styles.css",
            media_type="text/css",
            headers=dict(_DASH_NO_CACHE),
        )

    @app.get("/favicon.svg", include_in_schema=False)
    async def dashboard_favicon_svg() -> FileResponse:
        return FileResponse(
            dashboard_path / "favicon.svg",
            media_type="image/svg+xml",
            headers=dict(_DASH_NO_CACHE),
        )
