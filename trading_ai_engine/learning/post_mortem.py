from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_ai_engine.market_yfinance import history_range
from trading_ai_engine.server import db
from trading_ai_engine.learning.refinement import append_outcome


def _parse_finding_time(iso: str) -> datetime:
    s = str(iso).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def run_post_mortem(
    finding_id: str,
    *,
    horizon_bars: int = 5,
    forward_threshold: float = 0.001,
) -> dict[str, Any]:
    """
    After the fact: compare stored brain stance to realised forward return over ``horizon_bars``
    daily bars from Yahoo (placeholder until Dhan history is wired the same way).

    Records an ``evolution`` event and appends to the self-learning state file for
    ``refinement_score_nudge`` on subsequent runs.
    """
    fid = str(finding_id or "").strip()
    try:
        uuid.UUID(fid)
    except ValueError:
        return {"status": "error", "message": "finding_id must be a UUID"}

    row = db.get_finding(fid)
    if not row:
        return {"status": "error", "message": "unknown finding_id"}

    with db.connect() as cx:
        bd = cx.execute(
            """
            SELECT fused_json FROM brain_decisions
            WHERE finding_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (fid,),
        ).fetchone()
    if not bd:
        return {"status": "error", "message": "no brain_decision for finding"}

    fused = json.loads(str(bd["fused_json"] or "{}"))
    action = str(fused.get("action") or "neutral")
    pred = 1 if action == "bullish" else -1 if action == "bearish" else 0

    symbol = str(row["symbol"])
    t0 = _parse_finding_time(str(row["created_at"]))
    start = t0.date().isoformat()
    end = (t0 + timedelta(days=max(42, horizon_bars * 10))).date().isoformat()

    ohlc = history_range(symbol, start=start, end=end, interval="1d")
    if ohlc.empty or "close" not in ohlc.columns:
        return {"status": "error", "message": "no forward price data", "symbol": symbol}

    ohlc = ohlc.sort_values("date").reset_index(drop=True)
    ohlc["date"] = ohlc["date"].dt.normalize()
    anchor = t0.date()
    mask = ohlc["date"].dt.date >= anchor
    sub = ohlc.loc[mask].reset_index(drop=True)
    if len(sub) < horizon_bars + 1:
        return {
            "status": "insufficient_forward_bars",
            "symbol": symbol,
            "bars_available": int(len(sub)),
            "horizon_bars": horizon_bars,
        }

    c0 = float(sub.iloc[0]["close"])
    c1 = float(sub.iloc[horizon_bars]["close"])
    fwd_ret = (c1 / c0 - 1.0) if c0 else 0.0
    outcome = 1 if fwd_ret > forward_threshold else -1 if fwd_ret < -forward_threshold else 0

    if pred == 0 or outcome == 0:
        correct: bool | None = None
        verdict = "skipped_neutral_zone"
    else:
        correct = pred == outcome
        verdict = "correct" if correct else "wrong"

    payload = {
        "finding_id": fid,
        "symbol": symbol,
        "brain_action": action,
        "forward_return": round(fwd_ret, 6),
        "horizon_bars": horizon_bars,
        "verdict": verdict,
        "correct": correct,
    }
    db.insert_evolution_event(
        symbol=symbol,
        event_type="post_mortem",
        score_delta=float(fwd_ret),
        payload=payload,
    )
    append_outcome(
        {
            "finding_id": fid,
            "symbol": symbol,
            "action": action,
            "forward_return": fwd_ret,
            "correct": correct,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {"status": "ok", **payload}
