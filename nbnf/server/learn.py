from __future__ import annotations

from datetime import datetime, timezone

from nbnf.server import db


ALPHA = 0.15  # EMA smoothing for reward signal from user feedback


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_feedback(finding_id: str, rating: int) -> dict[str, float] | None:
    """
    Nudge per-tag EMAs from structured feedback (rating in {-1, 0, +1}).
    Returns updated tag -> ema for the finding's symbol, or None if unknown finding.
    """
    r = max(-1, min(1, int(rating)))
    row = db.get_finding(finding_id)
    if not row:
        return None
    symbol = row["symbol"]
    tags: list[str] = row["tags"]
    if not tags:
        db.insert_feedback(finding_id, r, None)
        return db.get_tag_emas(symbol)

    with db.connect() as cx:
        for tag in tags:
            cur = cx.execute(
                "SELECT ema, n FROM signal_stats WHERE symbol = ? AND tag = ?",
                (symbol, tag),
            ).fetchone()
            if cur is None:
                ema = float(r)
                n = 1
            else:
                prev = float(cur["ema"])
                ema = (1 - ALPHA) * prev + ALPHA * float(r)
                n = int(cur["n"]) + 1
            cx.execute(
                """
                INSERT INTO signal_stats (symbol, tag, ema, n, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, tag) DO UPDATE SET
                    ema = excluded.ema,
                    n = excluded.n,
                    updated_at = excluded.updated_at
                """,
                (symbol, tag, ema, n, _utc_now()),
            )
        cx.execute(
            "INSERT INTO feedback (finding_id, rating, note, created_at) VALUES (?, ?, ?, ?)",
            (finding_id, r, None, _utc_now()),
        )
    return db.get_tag_emas(symbol)
