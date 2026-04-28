from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nbnf.dhan.config import dhan_readiness
from nbnf.dhan.market_feed import market_feed_status
from nbnf.india.market_clock import market_snapshot
from nbnf.ml.ingest import scan_and_ingest
from nbnf.options.local_chain import local_option_chain_heatmap
from nbnf.quanttape.briefing import build_briefing
from nbnf.server import analyze, db, learn
from nbnf.server.research import run_symbol_backtest

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
        symbol = str(payload.get("symbol") or "").strip()
        period = str(payload.get("period") or "1y")
        use_llm = bool(payload.get("use_llm", True))
        include_yahoo_deep = bool(payload.get("include_yahoo_deep", True))
        include_ml_digest = bool(payload.get("include_ml_digest", True))
        if not symbol:
            await ws.send_json({"type": "error", "message": "symbol is required"})
            return
        await ws.send_json({"type": "status", "message": f"Fetching and scoring {symbol}..."})
        try:
            result = await asyncio.to_thread(
                lambda: analyze.run_analyze(
                    symbol,
                    period,
                    use_llm=use_llm,
                    include_yahoo_deep=include_yahoo_deep,
                    include_ml_digest=include_ml_digest,
                )
            )
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "finding", **result})
        await ws.send_json({"type": "learning_update", **result["learning"]})
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

    if ptype == "research_backtest":
        symbol = str(payload.get("symbol") or "").strip()
        period = str(payload.get("period") or "5y")
        horizon = int(payload.get("horizon_bars") or 5)
        if not symbol:
            await ws.send_json({"type": "error", "message": "symbol is required"})
            return
        await ws.send_json({"type": "status", "message": f"Backtesting {symbol} over {period}..."})
        try:
            result = await asyncio.to_thread(
                lambda: run_symbol_backtest(symbol, period=period, horizon_bars=horizon)
            )
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "research_backtest", **result})
        return

    if ptype == "options_heatmap":
        underlying = str(payload.get("underlying") or "nifty").strip() or "nifty"
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
        include_ml_digest = bool(payload.get("include_ml_digest", True))
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
            await ws.send_json({"type": "status", "message": f"Sweep: {sym}..."})
            try:
                result = await asyncio.to_thread(
                    lambda s=sym: analyze.run_analyze(
                        s,
                        period,
                        use_llm=use_llm,
                        include_yahoo_deep=include_yahoo_deep,
                        include_ml_digest=include_ml_digest,
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
            "message": "QuantTape online - NSE/BSE context (IST), profile catalog + brain ready.",
            "quanttape": True,
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
