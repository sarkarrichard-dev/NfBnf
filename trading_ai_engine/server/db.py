from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from trading_ai_engine.server.paths import DB_PATH, DATA_DIR

_IST = ZoneInfo("Asia/Kolkata")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_schema(cx: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in cx.execute("PRAGMA table_info(paper_orders)").fetchall()}
    if "model_version" not in cols:
        cx.execute("ALTER TABLE paper_orders ADD COLUMN model_version TEXT")


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
            CREATE TABLE IF NOT EXISTS paper_orders (
                id TEXT PRIMARY KEY,
                finding_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL,
                target REAL,
                notional REAL NOT NULL,
                risk_amount REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                brain_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (finding_id) REFERENCES findings(id)
            );
            CREATE TABLE IF NOT EXISTS evolution_events (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                score_delta REAL NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _migrate_schema(cx)
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


def learning_context(symbol: str, *, recent_limit: int = 12) -> dict[str, Any]:
    """Compact closed-loop memory passed into the next brain run."""
    with connect() as cx:
        symbol_rows = cx.execute(
            """
            SELECT tag, ema, n, updated_at
            FROM signal_stats
            WHERE symbol = ?
            ORDER BY tag
            """,
            (symbol,),
        ).fetchall()
        global_rows = cx.execute(
            """
            SELECT tag, AVG(ema) AS ema, SUM(n) AS n, MAX(updated_at) AS updated_at
            FROM signal_stats
            GROUP BY tag
            ORDER BY tag
            """
        ).fetchall()
        totals = cx.execute(
            """
            SELECT
                COUNT(*) AS feedback_count,
                COALESCE(AVG(rating), 0.0) AS avg_rating,
                SUM(CASE WHEN rating > 0 THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN rating < 0 THEN 1 ELSE 0 END) AS negative,
                SUM(CASE WHEN rating = 0 THEN 1 ELSE 0 END) AS neutral
            FROM feedback fb
            JOIN findings f ON f.id = fb.finding_id
            WHERE f.symbol = ?
            """,
            (symbol,),
        ).fetchone()
        recent = cx.execute(
            """
            SELECT
                fb.rating,
                fb.created_at,
                f.symbol,
                f.tags_json,
                f.bias,
                bd.fused_json
            FROM feedback fb
            JOIN findings f ON f.id = fb.finding_id
            LEFT JOIN brain_decisions bd ON bd.finding_id = f.id
            WHERE f.symbol = ?
            ORDER BY fb.created_at DESC
            LIMIT ?
            """,
            (symbol, recent_limit),
        ).fetchall()

    symbol_stats = [dict(r) for r in symbol_rows]
    global_stats = [dict(r) for r in global_rows]
    symbol_emas = {str(r["tag"]): float(r["ema"]) for r in symbol_stats}
    global_emas = {str(r["tag"]): float(r["ema"]) for r in global_stats}
    blended: dict[str, float] = dict(global_emas)
    blended.update(symbol_emas)

    recent_feedback: list[dict[str, Any]] = []
    for row in recent:
        d = dict(row)
        try:
            d["tags"] = json.loads(str(d.pop("tags_json") or "[]"))
        except json.JSONDecodeError:
            d["tags"] = []
        fused_raw = d.pop("fused_json", None)
        if fused_raw:
            try:
                fused = json.loads(str(fused_raw))
            except json.JSONDecodeError:
                fused = {}
            d["previous_action"] = fused.get("action")
            d["previous_score"] = fused.get("score")
            d["previous_confidence"] = fused.get("confidence")
        recent_feedback.append(d)

    t = dict(totals) if totals else {}
    return {
        "symbol": symbol,
        "tag_emas": blended,
        "symbol_tag_stats": symbol_stats,
        "global_tag_stats": global_stats,
        "recent_feedback": recent_feedback,
        "feedback_summary": {
            "count": int(t.get("feedback_count") or 0),
            "avg_rating": float(t.get("avg_rating") or 0.0),
            "positive": int(t.get("positive") or 0),
            "negative": int(t.get("negative") or 0),
            "neutral": int(t.get("neutral") or 0),
        },
    }


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


def insert_paper_order(order: dict[str, Any]) -> str:
    oid = str(uuid.uuid4())
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO paper_orders (
                id, finding_id, symbol, side, quantity, entry_price, stop_loss, target,
                notional, risk_amount, status, reason, plan_json, brain_json, created_at,
                model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                oid,
                order.get("finding_id"),
                order["symbol"],
                order["side"],
                int(order["quantity"]),
                float(order["entry_price"]),
                order.get("stop_loss"),
                order.get("target"),
                float(order["notional"]),
                float(order["risk_amount"]),
                order["status"],
                order.get("reason") or "",
                json.dumps(order.get("plan") or {}, default=str),
                json.dumps(order.get("brain") or {}, default=str),
                _utc_now(),
                order.get("model_version"),
            ),
        )
    return oid


def fetch_paper_orders(*, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        for key in ("plan_json", "brain_json"):
            raw = d.pop(key, "{}")
            try:
                d[key.removesuffix("_json")] = json.loads(str(raw or "{}"))
            except json.JSONDecodeError:
                d[key.removesuffix("_json")] = {}
        out.append(d)
    return out


def _parse_created_utc(iso: str) -> datetime:
    s = str(iso).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def paper_stats_current_ist_day() -> dict[str, Any]:
    """Orders placed since midnight IST today (UTC timestamps in DB)."""
    now_ist = datetime.now(_IST)
    start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_ist.astimezone(timezone.utc).isoformat()
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT risk_amount, created_at FROM paper_orders
            WHERE created_at >= ?
            """,
            (start_utc,),
        ).fetchall()
    risk = sum(float(r["risk_amount"] or 0) for r in rows)
    return {
        "ist_date": now_ist.date().isoformat(),
        "orders_today": len(rows),
        "risk_amount_today": round(risk, 2),
    }


def paper_distinct_ist_session_days() -> int:
    """Count of IST calendar days with at least one paper order."""
    with connect() as cx:
        rows = cx.execute("SELECT created_at FROM paper_orders").fetchall()
    days: set[str] = set()
    for r in rows:
        dt = _parse_created_utc(str(r["created_at"]))
        days.add(dt.astimezone(_IST).date().isoformat())
    return len(days)


def paper_trading_summary() -> dict[str, Any]:
    with connect() as cx:
        totals = cx.execute(
            """
            SELECT
                COUNT(*) AS orders,
                COALESCE(SUM(notional), 0.0) AS notional,
                COALESCE(SUM(risk_amount), 0.0) AS risk_amount,
                MIN(created_at) AS first_order_at,
                MAX(created_at) AS last_order_at
            FROM paper_orders
            """
        ).fetchone()
        by_side = cx.execute(
            """
            SELECT side, COUNT(*) AS orders, COALESCE(SUM(notional), 0.0) AS notional
            FROM paper_orders
            GROUP BY side
            ORDER BY orders DESC
            """
        ).fetchall()
    t = dict(totals) if totals else {}
    return {
        "orders": int(t.get("orders") or 0),
        "notional": float(t.get("notional") or 0.0),
        "risk_amount": float(t.get("risk_amount") or 0.0),
        "first_order_at": t.get("first_order_at"),
        "last_order_at": t.get("last_order_at"),
        "by_side": [dict(r) for r in by_side],
        "mode": "paper_only",
    }


def insert_evolution_event(
    *,
    symbol: str,
    event_type: str,
    score_delta: float,
    payload: dict[str, Any],
) -> str:
    eid = str(uuid.uuid4())
    with connect() as cx:
        cx.execute(
            """
            INSERT INTO evolution_events (id, symbol, event_type, score_delta, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (eid, symbol, event_type, float(score_delta), json.dumps(payload, default=str), _utc_now()),
        )
    return eid


def fetch_evolution_events(symbol: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as cx:
        if symbol:
            rows = cx.execute(
                """
                SELECT * FROM evolution_events
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        else:
            rows = cx.execute(
                "SELECT * FROM evolution_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        raw = d.pop("payload_json", "{}")
        try:
            d["payload"] = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            d["payload"] = {}
        out.append(d)
    return out


def evolution_summary(symbol: str | None = None) -> dict[str, Any]:
    with connect() as cx:
        if symbol:
            params: tuple[Any, ...] = (symbol,)
            where = "WHERE symbol = ?"
        else:
            params = ()
            where = ""
        totals = cx.execute(
            f"""
            SELECT
                COUNT(*) AS events,
                COALESCE(AVG(score_delta), 0.0) AS avg_score_delta,
                MIN(created_at) AS first_event_at,
                MAX(created_at) AS last_event_at
            FROM evolution_events
            {where}
            """,
            params,
        ).fetchone()
        by_type = cx.execute(
            f"""
            SELECT event_type, COUNT(*) AS events, COALESCE(AVG(score_delta), 0.0) AS avg_score_delta
            FROM evolution_events
            {where}
            GROUP BY event_type
            ORDER BY events DESC
            """,
            params,
        ).fetchall()
    t = dict(totals) if totals else {}
    return {
        "symbol": symbol,
        "events": int(t.get("events") or 0),
        "avg_score_delta": float(t.get("avg_score_delta") or 0.0),
        "first_event_at": t.get("first_event_at"),
        "last_event_at": t.get("last_event_at"),
        "by_type": [dict(r) for r in by_type],
    }
