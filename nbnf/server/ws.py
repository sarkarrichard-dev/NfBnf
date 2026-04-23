from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nbnf.server import analyze, db, learn

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
        if not symbol:
            await ws.send_json({"type": "error", "message": "symbol is required"})
            return
        await ws.send_json({"type": "status", "message": f"Fetching and scoring {symbol}…"})
        try:
            result = await asyncio.to_thread(analyze.run_analyze, symbol, period, use_llm=use_llm)
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

    await ws.send_json({"type": "error", "message": f"unknown type: {ptype}"})


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json({"type": "hello", "message": "connected"})
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
