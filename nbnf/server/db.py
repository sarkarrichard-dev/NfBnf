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
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT NOT NULL PRIMARY KEY,
                added_at TEXT NOT NULL,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS ml_datasets (
                rel_path TEXT PRIMARY KEY,
                format TEXT NOT NULL,
                bytes INTEGER,
                mtime TEXT,
                rows_profiled INTEGER NOT NULL,
                columns_json TEXT NOT NULL,
                stats_json TEXT NOT NULL,
                sample_head_json TEXT NOT NULL,
                error TEXT,
                ingested_at TEXT NOT NULL
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


def watchlist_add(symbol: str, note: str | None = None) -> None:
    sym = symbol.strip()
    if not sym:
        return
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO watchlist (symbol, added_at, note)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note = COALESCE(excluded.note, watchlist.note)
            """,
            (sym, _utc_now(), note),
        )


def watchlist_remove(symbol: str) -> bool:
    sym = symbol.strip()
    if not sym:
        return False
    with connect() as cx:
        cur = cx.execute("DELETE FROM watchlist WHERE symbol = ?", (sym,))
        return cur.rowcount > 0


def watchlist_list() -> list[str]:
    with connect() as cx:
        rows = cx.execute("SELECT symbol FROM watchlist ORDER BY added_at ASC").fetchall()
    return [str(r["symbol"]) for r in rows]


def upsert_ml_dataset(row: dict[str, Any]) -> None:
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO ml_datasets (
                rel_path, format, bytes, mtime, rows_profiled,
                columns_json, stats_json, sample_head_json, error, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                format = excluded.format,
                bytes = excluded.bytes,
                mtime = excluded.mtime,
                rows_profiled = excluded.rows_profiled,
                columns_json = excluded.columns_json,
                stats_json = excluded.stats_json,
                sample_head_json = excluded.sample_head_json,
                error = excluded.error,
                ingested_at = excluded.ingested_at
            """,
            (
                row["rel_path"],
                row["format"],
                row.get("bytes"),
                row.get("mtime"),
                int(row.get("rows_profiled") or 0),
                row["columns_json"],
                row["stats_json"],
                row["sample_head_json"],
                row.get("error"),
                _utc_now(),
            ),
        )


def fetch_ml_datasets(*, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT * FROM ml_datasets ORDER BY ingested_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def ml_datasets_count() -> int:
    with connect() as cx:
        row = cx.execute("SELECT COUNT(*) AS c FROM ml_datasets").fetchone()
    return int(row["c"]) if row else 0


def ml_datasets_summary() -> dict[str, Any]:
    """Operator-facing audit of the local tabular profile catalog."""
    with connect() as cx:
        totals = cx.execute(
            """
            SELECT
                COUNT(*) AS files,
                COALESCE(SUM(bytes), 0) AS bytes,
                COALESCE(SUM(rows_profiled), 0) AS rows_profiled,
                SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS errors,
                MIN(ingested_at) AS first_ingested_at,
                MAX(ingested_at) AS last_ingested_at
            FROM ml_datasets
            """
        ).fetchone()
        by_format = cx.execute(
            """
            SELECT
                format,
                COUNT(*) AS files,
                COALESCE(SUM(bytes), 0) AS bytes,
                COALESCE(SUM(rows_profiled), 0) AS rows_profiled,
                SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS errors
            FROM ml_datasets
            GROUP BY format
            ORDER BY files DESC
            """
        ).fetchall()
        recent_errors = cx.execute(
            """
            SELECT rel_path, error, ingested_at
            FROM ml_datasets
            WHERE error IS NOT NULL AND error <> ''
            ORDER BY ingested_at DESC
            LIMIT 20
            """
        ).fetchall()

    t = dict(totals) if totals else {}
    return {
        "files": int(t.get("files") or 0),
        "bytes": int(t.get("bytes") or 0),
        "rows_profiled": int(t.get("rows_profiled") or 0),
        "errors": int(t.get("errors") or 0),
        "first_ingested_at": t.get("first_ingested_at"),
        "last_ingested_at": t.get("last_ingested_at"),
        "by_format": [dict(r) for r in by_format],
        "recent_errors": [dict(r) for r in recent_errors],
        "mode": "profile_catalog",
        "meaning": (
            "Local files are profiled into SQLite. This is not model training; analysis receives "
            "a capped text digest of dataset paths, columns, row counts, and samples."
        ),
    }


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
