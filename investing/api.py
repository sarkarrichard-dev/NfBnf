"""Investing section API — read-only, no orders, no money path."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/investing", tags=["investing"])


@router.get("/screen")
async def screen(limit: int = 40) -> dict:
    from investing.screener import run_screen

    try:
        rows = await asyncio.to_thread(run_screen, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"rows": rows, "count": len(rows)}
