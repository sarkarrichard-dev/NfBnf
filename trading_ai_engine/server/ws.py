from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from trading_ai_engine.dhan.config import dhan_readiness
from trading_ai_engine.dhan.market_feed import market_feed_status
from trading_ai_engine.india.market_clock import market_snapshot
from trading_ai_engine.india.nse_yahoo import normalize_nse_yahoo_symbol, require_nifty_option_underlying
from trading_ai_engine.ml.ingest import scan_and_ingest
from trading_ai_engine.ml.market_learn import (
    InternetDatasetConfig,
    build_market_training_frame,
    download_indian_market_history,
    learning_status,
    train_market_model,
)
from trading_ai_engine.options.local_chain import local_option_chain_heatmap
from trading_ai_engine.ai_voice.briefing import build_briefing
from trading_ai_engine.server import analyze, db, learn
from trading_ai_engine.server.research import run_symbol_backtest
from trading_ai_engine.trading.evolution import evolution_snapshot
from trading_ai_engine.learning.post_mortem import run_post_mortem
from trading_ai_engine.trading.paper import place_paper_order, recent_paper_orders

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _handle_payload(ws: WebSocket, payload: dict[str, Any]) -> None:
    ptype = payload.get("type")
    if ptype == "ping":
        await ws.send_json({"type": "pong"})
        return

    if ptype == "analyze":
        symbol = str(payload.get("symbol") or "^NSEI").strip()
        period = str(payload.get("period") or "1y")
        interval = str(payload.get("interval") or "1d")
        market_focus = str(payload.get("market_focus") or os.environ.get("TRADING_AI_MARKET_FOCUS") or "balanced")
        use_llm = bool(payload.get("use_llm", True))
        include_yahoo_deep = bool(payload.get("include_yahoo_deep", True))
        include_ml_digest = bool(payload.get("include_ml_digest", False))
        include_heatmap = bool(payload.get("include_heatmap", False))
        heatmap_underlying = str(payload.get("heatmap_underlying") or "nifty")
        heatmap_source = str(payload.get("heatmap_source") or "auto")
        await ws.send_json({"type": "status", "message": f"Fetching and scoring {symbol}..."})
        try:
            result = await asyncio.to_thread(
                lambda: analyze.run_analyze(
                    symbol,
                    period,
                    use_llm=use_llm,
                    include_yahoo_deep=include_yahoo_deep,
                    include_ml_digest=include_ml_digest,
                    include_heatmap=include_heatmap,
                    heatmap_underlying=heatmap_underlying,
                    heatmap_source=heatmap_source,
                    interval=interval,
                    market_focus=market_focus,
                    include_global_context=bool(payload.get("include_global_context", True)),
                    include_hf_online_digest=bool(payload.get("include_hf_online_digest", True)),
                    include_strategy_features=bool(payload.get("include_strategy_features", True)),
                    include_dhan_snapshot=bool(payload.get("include_dhan_snapshot", True)),
                )
            )
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "finding", **result})
        await ws.send_json({"type": "learning_update", **result["learning"]})
        return

    if ptype == "paper_order":
        finding_id = str(payload.get("finding_id") or "")
        symbol = str(payload.get("symbol") or "").strip()
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        brain = payload.get("brain") if isinstance(payload.get("brain"), dict) else {}
        if not finding_id or not symbol or not plan:
            await ws.send_json(
                {"type": "error", "message": "finding_id, symbol, and plan are required"}
            )
            return
        try:
            symbol = normalize_nse_yahoo_symbol(symbol)
        except ValueError as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        result = await asyncio.to_thread(
            lambda: place_paper_order(
                finding_id=finding_id,
                symbol=symbol,
                plan=plan,
                brain=brain,
            )
        )
        await ws.send_json({"type": "paper_order_result", **result})
        await ws.send_json({"type": "paper_orders", **await asyncio.to_thread(recent_paper_orders)})
        await ws.send_json({"type": "evolution", **await asyncio.to_thread(lambda: evolution_snapshot(symbol))})
        return

    if ptype == "paper_orders":
        await ws.send_json({"type": "paper_orders", **await asyncio.to_thread(recent_paper_orders)})
        return

    if ptype == "post_mortem":
        finding_id = str(payload.get("finding_id") or "")
        if not finding_id:
            await ws.send_json({"type": "error", "message": "finding_id is required"})
            return
        horizon = int(payload.get("horizon_bars") or 5)
        try:
            out = await asyncio.to_thread(lambda: run_post_mortem(finding_id, horizon_bars=horizon))
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "post_mortem_result", **out})
        if out.get("status") == "ok":
            sym = str(out.get("symbol") or "").strip() or None
            await ws.send_json(
                {"type": "evolution", **await asyncio.to_thread(lambda: evolution_snapshot(sym))}
            )
        return

    if ptype == "evolution":
        symbol = str(payload.get("symbol") or "").strip() or None
        snap = await asyncio.to_thread(lambda: evolution_snapshot(symbol))
        await ws.send_json({"type": "evolution", **snap})
        return

    if ptype == "feedback":
        finding_id = str(payload.get("finding_id") or "")
        rating = payload.get("rating")
        if not finding_id or rating is None:
            await ws.send_json({"type": "error", "message": "finding_id and rating are required"})
            return
        try:
            r = int(rating)
        except (TypeError, ValueError):
            await ws.send_json({"type": "error", "message": "rating must be an integer"})
            return
        updated = await asyncio.to_thread(learn.apply_feedback, finding_id, r)
        if updated is None:
            await ws.send_json({"type": "error", "message": "unknown finding_id"})
            return
        row = db.get_finding(finding_id)
        sym = row["symbol"] if row else None
        snap = db.learning_snapshot(sym) if sym else db.learning_snapshot(None)
        await ws.send_json(
            {
                "type": "feedback_ack",
                "finding_id": finding_id,
                "rating": r,
                "tag_emas": updated,
            }
        )
        await ws.send_json({"type": "learning_update", **snap})
        if sym:
            evo = await asyncio.to_thread(lambda: evolution_snapshot(sym))
            await ws.send_json({"type": "evolution", **evo})
        return

    if ptype == "learning_state":
        symbol = str(payload.get("symbol") or "").strip() or None
        snap = db.learning_snapshot(symbol)
        await ws.send_json({"type": "learning_update", **snap})
        return

    if ptype == "brief":
        use_llm = bool(payload.get("use_llm", True))
        brief = await asyncio.to_thread(build_briefing, use_llm=use_llm)
        await ws.send_json(brief)
        return

    if ptype == "watchlist_add":
        sym = str(payload.get("symbol") or "").strip()
        if not sym:
            await ws.send_json({"type": "error", "message": "symbol is required"})
            return
        note = str(payload.get("note") or "").strip() or None
        await asyncio.to_thread(db.watchlist_add, sym, note)
        syms = await asyncio.to_thread(db.watchlist_list)
        await ws.send_json({"type": "watchlist", "symbols": syms})
        return

    if ptype == "watchlist_remove":
        sym = str(payload.get("symbol") or "").strip()
        if not sym:
            await ws.send_json({"type": "error", "message": "symbol is required"})
            return
        await asyncio.to_thread(db.watchlist_remove, sym)
        syms = await asyncio.to_thread(db.watchlist_list)
        await ws.send_json({"type": "watchlist", "symbols": syms})
        return

    if ptype == "watchlist_list":
        syms = await asyncio.to_thread(db.watchlist_list)
        await ws.send_json({"type": "watchlist", "symbols": syms})
        return

    if ptype == "ingest_ml_data":
        await ws.send_json({"type": "status", "message": "Scanning local profile data folders..."})
        try:
            summary = await asyncio.to_thread(scan_and_ingest)
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "ml_ingest_done", **summary})
        return

    if ptype == "ml_datasets_list":
        rows = await asyncio.to_thread(lambda: db.fetch_ml_datasets(limit=500))
        n = await asyncio.to_thread(db.ml_datasets_count)
        summary = await asyncio.to_thread(db.ml_datasets_summary)
        await ws.send_json({"type": "ml_datasets", "count": n, "summary": summary, "datasets": rows})
        return

    if ptype == "market_learning_status":
        await ws.send_json({"type": "market_learning_status", **await asyncio.to_thread(learning_status)})
        return

    if ptype == "market_learning_download":
        max_symbols = int(payload.get("max_symbols") or 80)
        years = int(payload.get("years") or 7)
        await ws.send_json(
            {
                "type": "status",
                "message": f"Downloading {years}y Indian market daily data for up to {max_symbols} symbols...",
            }
        )
        cfg = InternetDatasetConfig(years=years, period=f"{years}y", max_symbols=max_symbols)
        result = await asyncio.to_thread(lambda: download_indian_market_history(config=cfg))
        frame = await asyncio.to_thread(lambda: build_market_training_frame(cfg))
        await ws.send_json({"type": "market_learning_downloaded", "download": result, "frame": frame})
        await ws.send_json({"type": "market_learning_status", **await asyncio.to_thread(learning_status)})
        return

    if ptype == "market_learning_train":
        await ws.send_json({"type": "status", "message": "Training local market model..."})
        result = await asyncio.to_thread(train_market_model)
        await ws.send_json({"type": "market_learning_trained", **result})
        await ws.send_json({"type": "market_learning_status", **await asyncio.to_thread(learning_status)})
        return

    if ptype == "research_backtest":
        symbol = str(payload.get("symbol") or "^NSEI").strip()
        period = str(payload.get("period") or "5y")
        interval = str(payload.get("interval") or "1d")
        horizon = int(payload.get("horizon_bars") or 5)
        cost_bps = float(payload.get("cost_bps") or 8.0)
        spread_bps = float(payload.get("spread_bps") or 0.0)
        signal_mode = str(payload.get("signal_mode") or "structural")
        fast_ma = int(payload.get("fast_ma") or 20)
        slow_ma = int(payload.get("slow_ma") or 50)
        z_lookback = int(payload.get("z_lookback") or 20)
        z_entry = float(payload.get("z_entry") or 1.0)
        try:
            symbol = normalize_nse_yahoo_symbol(symbol)
        except ValueError as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "status", "message": f"Backtesting {symbol} over {period}..."})
        try:
            result = await asyncio.to_thread(
                lambda: run_symbol_backtest(
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
            )
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "research_backtest", **result})
        return

    if ptype == "options_heatmap":
        try:
            underlying = require_nifty_option_underlying(str(payload.get("underlying") or "nifty"))
        except ValueError as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        trade_date = str(payload.get("trade_date") or "").strip() or None
        result = await asyncio.to_thread(
            lambda: local_option_chain_heatmap(underlying, trade_date=trade_date)
        )
        await ws.send_json({"type": "options_heatmap", **result})
        return

    if ptype == "dhan_status":
        ready = await asyncio.to_thread(dhan_readiness)
        feed = await asyncio.to_thread(market_feed_status)
        await ws.send_json({"type": "dhan_status", "readiness": ready, "feed": feed})
        return

    if ptype == "sweep":
        period = str(payload.get("period") or "3mo")
        use_llm = bool(payload.get("use_llm", True))
        include_yahoo_deep = bool(payload.get("include_yahoo_deep", True))
        include_ml_digest = bool(payload.get("include_ml_digest", False))
        force = bool(payload.get("force", False))
        snap = await asyncio.to_thread(market_snapshot)
        if snap.get("phase") != "regular" and not force:
            await ws.send_json(
                {
                    "type": "error",
                    "message": (
                        "Sweep is meant during NSE regular cash session (IST). "
                        f"Current phase: {snap.get('phase')}. "
                        "Send {\"type\":\"sweep\",\"force\":true,...} to run anyway."
                    ),
                }
            )
            return
        wl = await asyncio.to_thread(db.watchlist_list)
        if not wl:
            await ws.send_json(
                {"type": "error", "message": "Watchlist is empty - add symbols first."}
            )
            return
        await ws.send_json(
            {
                "type": "sweep_start",
                "symbols": wl,
                "count": len(wl),
                "period": period,
                "india": snap,
            }
        )
        for sym in wl:
            try:
                sym_n = normalize_nse_yahoo_symbol(sym)
            except ValueError as e:
                await ws.send_json({"type": "sweep_error", "symbol": sym, "message": str(e)})
                continue
            await ws.send_json({"type": "status", "message": f"Sweep: {sym_n}..."})
            try:
                result = await asyncio.to_thread(
                    lambda s=sym_n: analyze.run_analyze(
                        s,
                        period,
                        use_llm=use_llm,
                        include_yahoo_deep=include_yahoo_deep,
                        include_ml_digest=include_ml_digest,
                        include_global_context=bool(payload.get("include_global_context", True)),
                        include_hf_online_digest=bool(payload.get("include_hf_online_digest", True)),
                        include_strategy_features=bool(payload.get("include_strategy_features", True)),
                        include_dhan_snapshot=bool(payload.get("include_dhan_snapshot", True)),
                    )
                )
            except Exception as e:
                await ws.send_json({"type": "sweep_error", "symbol": sym, "message": str(e)})
                continue
            await ws.send_json({"type": "sweep_item", **result})
        learn_snap = await asyncio.to_thread(db.learning_snapshot, None)
        await ws.send_json({"type": "learning_update", **learn_snap})
        await ws.send_json({"type": "sweep_done", "count": len(wl)})
        return

    await ws.send_json({"type": "error", "message": f"unknown type: {ptype}"})


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json(
        {
            "type": "hello",
            "message": "Trading AI Workstation online - NSE/BSE context (IST), profile catalog + brain ready.",
            "Trading AI Workstation": True,
        }
    )
    try:
        brief = await asyncio.to_thread(build_briefing, use_llm=True)
        await ws.send_json(brief)
    except Exception as e:
        await ws.send_json({"type": "error", "message": f"briefing failed: {e}"})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "invalid JSON"})
                continue
            if not isinstance(payload, dict):
                await ws.send_json({"type": "error", "message": "payload must be a JSON object"})
                continue
            await _handle_payload(ws, payload)
    except WebSocketDisconnect:
        manager.disconnect(ws)
