from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from nbnf.india.constituents import get_indices_catalog
from nbnf.server import db
from nbnf.server.paths import STATIC_DIR
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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> RedirectResponse:
    """Browsers request /favicon.ico by default; avoid 404 noise in logs."""
    if _favicon_svg.is_file():
        return RedirectResponse(url="/favicon.svg", status_code=307)
    return RedirectResponse(url="/", status_code=302)


if static_path.is_dir():
    app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")
