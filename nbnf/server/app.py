from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
if static_path.is_dir():
    app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")
