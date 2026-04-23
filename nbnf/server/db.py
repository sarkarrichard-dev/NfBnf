from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from nbnf.server.paths import DB_PATH, DATA_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("PRAGMA journal_mode=WAL;")
        cx.executescript(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                bias REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (finding_id) REFERENCES findings(id)
            );
            CREATE TABLE IF NOT EXISTS signal_stats (
                symbol TEXT NOT NULL,
                tag TEXT NOT NULL,
                ema REAL NOT NULL,
                n INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, tag)
            );
            CREATE TABLE IF NOT EXISTS brain_decisions (
                id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ml_json TEXT NOT NULL,
                ai_json TEXT NOT NULL,
                fused_json TEXT NOT NULL,
                FOREIGN KEY (finding_id) REFERENCES findings(id)
            );
            """
        )
        cx.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init_db()
    cx = sqlite3.connect(DB_PATH, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def insert_finding(
    *,
    symbol: str,
    summary: str,
    tags: list[str],
    metrics: dict[str, Any],
    bias: float,
) -> str:
    fid = str(uuid.uuid4())
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO findings (id, symbol, created_at, summary, tags_json, metrics_json, bias)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (fid, symbol, _utc_now(), summary, json.dumps(tags), json.dumps(metrics, default=str), bias),
        )
    return fid


def insert_brain_decision(
    *,
    finding_id: str,
    symbol: str,
    ml: dict[str, Any],
    ai: dict[str, Any],
    fused: dict[str, Any],
) -> str:
    bid = str(uuid.uuid4())
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO brain_decisions (id, finding_id, symbol, created_at, ml_json, ai_json, fused_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid,
                finding_id,
                symbol,
                _utc_now(),
                json.dumps(ml, default=str),
                json.dumps(ai, default=str),
                json.dumps(fused, default=str),
            ),
        )
    return bid


def get_finding(finding_id: str) -> dict[str, Any] | None:
    with connect() as cx:
        row = cx.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tags"] = json.loads(d.pop("tags_json"))
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


def insert_feedback(finding_id: str, rating: int, note: str | None) -> None:
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO feedback (finding_id, rating, note, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (finding_id, rating, note, _utc_now()),
        )


def get_tag_emas(symbol: str) -> dict[str, float]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT tag, ema FROM signal_stats WHERE symbol = ?",
            (symbol,),
        ).fetchall()
    return {str(r["tag"]): float(r["ema"]) for r in rows}


def learning_snapshot(symbol: str | None) -> dict[str, Any]:
    with connect() as cx:
        if symbol:
            rows = cx.execute(
                "SELECT symbol, tag, ema, n, updated_at FROM signal_stats WHERE symbol = ? ORDER BY tag",
                (symbol,),
            ).fetchall()
        else:
            rows = cx.execute(
                "SELECT symbol, tag, ema, n, updated_at FROM signal_stats ORDER BY symbol, tag"
            ).fetchall()
    return {"signal_stats": [dict(r) for r in rows]}
