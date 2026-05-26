from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from index_ai.config import DB_PATH, MEMORY_DIR
from index_ai.market_clock import format_ist_display, now_ist_iso, today_ist_date


def now_utc() -> str:
    """Backward-compatible alias — timestamps are stored in IST."""
    return now_ist_iso()


def init_db() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                instrument TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                option_json TEXT NOT NULL,
                signal_json TEXT NOT NULL,
                status TEXT NOT NULL,
                pnl REAL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                rating INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learned_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def record_trade(
    *,
    mode: str,
    instrument: str,
    action: str,
    confidence: float,
    option: dict[str, Any],
    signal: dict[str, Any],
    status: str,
) -> str:
    trade_id = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            """
            INSERT INTO trades
            (id, created_at, mode, instrument, action, confidence, option_json, signal_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                now_utc(),
                mode,
                instrument,
                action,
                confidence,
                json.dumps(option, default=str),
                json.dumps(signal, default=str),
                status,
            ),
        )
    return trade_id


def _row_to_trade(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["option"] = json.loads(item.pop("option_json"))
    item["signal"] = json.loads(item.pop("signal_json"))
    return item


def recent_trades(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def trades_summary() -> dict[str, Any]:
    today = today_ist_date()
    with connect() as db:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN mode = 'PAPER' OR status = 'PAPER_RECORDED' THEN 1 ELSE 0 END) AS paper_count,
                SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS closed_count,
                COALESCE(SUM(pnl), 0) AS total_pnl
            FROM trades
            """
        ).fetchone()
        today_row = db.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(pnl), 0) AS total_pnl
            FROM trades WHERE substr(created_at, 1, 10) = ?
            """,
            (today,),
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "paper_count": int(row["paper_count"] or 0),
        "closed_count": int(row["closed_count"] or 0),
        "total_pnl": float(row["total_pnl"] or 0),
        "today_count": int(today_row["total"] or 0),
        "today_pnl": float(today_row["total_pnl"] or 0),
    }


def save_exit_prices(
    trade_id: str,
    *,
    exit_option_ltp: float | None,
    exit_index_price: float | None = None,
) -> None:
    with connect() as db:
        row = db.execute("SELECT option_json FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return
        option = json.loads(row["option_json"])
        if exit_option_ltp is not None:
            option["exit_ltp"] = float(exit_option_ltp)
        if exit_index_price is not None:
            option["exit_index_price"] = float(exit_index_price)
        option["closed_at"] = now_ist_iso()
        db.execute(
            "UPDATE trades SET option_json = ? WHERE id = ?",
            (json.dumps(option, default=str), trade_id),
        )


def _resolve_option_side(option: dict[str, Any], action: str) -> str:
    raw = str(option.get("option_type") or "").upper()
    if raw in ("CALL", "CE"):
        return "CE"
    if raw in ("PUT", "PE"):
        return "PE"
    act = action.upper()
    if "CALL" in act:
        return "CE"
    if "PUT" in act:
        return "PE"
    return ""


def resolve_trade_lot_size(trade: dict[str, Any]) -> tuple[int, int]:
    """Return (configured units per lot, effective quantity for PnL)."""
    from index_ai.instruments import get_instrument
    from index_ai.risk_policy import HARDCODED_RISK

    option = trade.get("option") or {}
    inst_key = str(trade.get("instrument") or option.get("instrument") or "")
    stored = int(option.get("quantity") or 0)
    try:
        configured = int(get_instrument(inst_key).lot_size) * int(HARDCODED_RISK.lots_per_trade)
    except ValueError:
        configured = stored or 1
    effective = configured if configured else (stored or 1)
    return configured, effective


def reconcile_all_trade_lots() -> dict[str, int]:
    """Persist correct NSE lot quantity on every journal row (open and closed)."""
    updated = 0
    with connect() as db:
        rows = db.execute("SELECT id FROM trades").fetchall()
    for row in rows:
        trade_id = str(row["id"])
        with connect() as db:
            raw = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not raw:
            continue
        before_qty = int((json.loads(raw["option_json"]).get("quantity") or 0))
        synced = sync_option_lot_size(_row_to_trade(raw), persist=True)
        after_qty = int((synced.get("option") or {}).get("quantity") or 0)
        if after_qty and after_qty != before_qty:
            updated += 1
    return {"trades_checked": len(rows), "quantities_updated": updated}


def sync_option_lot_size(trade: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
    """Align stored option quantity with configured NSE lot (fixes pre-revision 75/35 rows)."""
    trade = dict(trade)
    option = dict(trade.get("option") or {})
    configured, effective = resolve_trade_lot_size(trade)
    if effective and int(option.get("quantity") or 0) != effective:
        option["quantity"] = effective
        trade["option"] = option
        if persist and trade.get("id"):
            from index_ai.mtm import persist_trade_option

            persist_trade_option(str(trade["id"]), option)
    return trade


def resolve_current_option_ltp(
    option: dict[str, Any],
    *,
    is_open: bool,
    mtm_pnl: float | None,
    entry_ltp: float | None,
    qty: int,
    tx: str,
) -> float | None:
    """Premium for exit column: closed exit, live LTP, history, or inferred from MTM."""
    if option.get("exit_ltp") is not None:
        return float(option["exit_ltp"])
    if option.get("last_option_ltp") is not None:
        return float(option["last_option_ltp"])
    hist = option.get("mtm_history") or []
    if hist and hist[-1].get("option_ltp") is not None:
        return float(hist[-1]["option_ltp"])
    if option.get("last_close_debit") is not None:
        return float(option["last_close_debit"])
    if is_open and mtm_pnl is not None and entry_ltp is not None and qty > 0:
        entry = float(entry_ltp)
        mtm = float(mtm_pnl)
        q = max(1, int(qty))
        legs = option.get("legs") or []
        if legs:
            return max(0.0, entry - mtm / q)
        if tx.upper() == "BUY":
            return entry + mtm / q
        if tx.upper() == "SELL":
            return entry - mtm / q
    return None


def _format_strike(strike: Any) -> str | None:
    if strike is None:
        return None
    value = float(strike)
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def option_leg_fields(trade: dict[str, Any]) -> dict[str, Any]:
    """Strike, CE/PE, and buy/sell labels for dashboards and analytics."""
    signal = trade.get("signal") or {}
    option = trade.get("option") or {}
    action = str(trade.get("action") or signal.get("action") or "")
    tx = str(option.get("transaction_type") or "BUY").upper()
    side_word = "Buy" if tx == "BUY" else "Sell"
    option_side = _resolve_option_side(option, action)
    strike_raw = option.get("strike")
    strike_display = _format_strike(strike_raw)
    opt_type = "CALL" if option_side == "CE" else "PUT" if option_side == "PE" else ""

    structure = str(option.get("structure") or "")
    legs = list(option.get("legs") or [])
    if structure and legs:
        from index_ai.credit_spread import format_legs_summary

        parts = [row["label"] for row in format_legs_summary(legs)]
        leg_display = f"{structure.replace('_', ' ')}: " + ", ".join(parts)
    elif structure:
        n_legs = len(legs)
        leg_display = f"Credit {structure.replace('_', ' ')}"
        if n_legs:
            leg_display += f" ({n_legs} legs)"
    elif strike_display and option_side:
        leg_display = f"{side_word} {strike_display} {option_side}"
    elif option_side:
        leg_display = f"{side_word} {option_side}"
    else:
        leg_display = action.replace("_", " ") if action else "—"

    instrument = str(trade.get("instrument") or option.get("instrument") or "")
    configured_lot, effective_qty = resolve_trade_lot_size(trade)
    from index_ai.risk_policy import HARDCODED_RISK

    lots = int(HARDCODED_RISK.lots_per_trade)
    lot_label = f"{lots} lot · {effective_qty} qty" if effective_qty else ""
    position_summary = instrument
    if leg_display and leg_display != "—":
        position_summary = f"{instrument} · {leg_display}" if instrument else leg_display
    if lot_label:
        position_summary = f"{position_summary} · {lot_label}"

    return {
        "transaction_type": tx,
        "side_word": side_word,
        "option_type": opt_type,
        "option_side": option_side,
        "strike": strike_raw,
        "strike_display": strike_display,
        "leg_display": leg_display,
        "position_summary": position_summary,
        "expiry": option.get("expiry"),
        "configured_lot_size": configured_lot,
        "quantity": effective_qty,
        "lot_label": lot_label,
        "structure": structure or None,
        "legs_detail": legs,
        "net_credit_points": option.get("net_credit_points"),
        "max_loss_rupees": option.get("max_loss_rupees"),
        "max_profit_rupees": option.get("max_profit_rupees"),
    }


def format_trade_for_ui(trade: dict[str, Any]) -> dict[str, Any]:
    """Flatten signal/option into dashboard-friendly entry/exit fields."""
    trade = sync_option_lot_size(trade)
    signal = trade.get("signal") or {}
    option = trade.get("option") or {}
    action = str(trade.get("action") or signal.get("action") or "")
    leg = option_leg_fields(trade)
    entry_price = signal.get("price")
    strike = option.get("strike")
    entry_ltp = option.get("ltp")
    segment = option.get("segment") or ""
    security_id = option.get("security_id")
    pnl = trade.get("pnl")
    mode = str(trade.get("mode") or "")
    status = str(trade.get("status") or "")
    exit_option_ltp = option.get("exit_ltp")
    exit_index_price = option.get("exit_index_price")
    mtm_pnl = option.get("mtm_pnl")
    last_ltp = option.get("last_option_ltp")
    mtm_updated = option.get("mtm_updated_at")
    mtm_history = option.get("mtm_history") or []
    is_open = pnl is None
    _, effective_qty = resolve_trade_lot_size(trade)
    qty = effective_qty or int(option.get("quantity") or 1)
    current_option_ltp = resolve_current_option_ltp(
        option,
        is_open=is_open,
        mtm_pnl=float(mtm_pnl) if mtm_pnl is not None else None,
        entry_ltp=float(entry_ltp) if entry_ltp is not None else None,
        qty=qty,
        tx=leg["transaction_type"],
    )

    if pnl is not None:
        exit_label = f"Closed @ ₹{float(exit_option_ltp):,.2f}" if exit_option_ltp else f"Closed (PnL ₹{float(pnl):,.2f})"
    elif mtm_pnl is not None:
        exit_label = "Open — live MTM"
    elif status == "PAPER_RECORDED" or mode == "PAPER":
        exit_label = "Open — waiting for MTM"
    else:
        exit_label = status or "—"

    tx = leg["transaction_type"]
    side_word = leg["side_word"]
    opt_type = leg["option_type"]
    created = trade.get("created_at")
    return {
        "id": trade.get("id"),
        "created_at": created,
        "created_at_ist": format_ist_display(str(created) if created else None),
        "instrument": trade.get("instrument") or option.get("instrument"),
        "action": action,
        "side_label": f"{side_word} {opt_type}".strip() if opt_type else leg["leg_display"],
        "leg_display": leg["leg_display"],
        "position_summary": leg["position_summary"],
        "option_side": leg["option_side"],
        "strike_display": leg["strike_display"],
        "expiry": leg["expiry"],
        "transaction_type": tx,
        "confidence": signal.get("confidence"),
        "mode": mode,
        "status": status,
        "pnl": pnl,
        "is_open": is_open,
        "mtm_pnl": float(mtm_pnl) if mtm_pnl is not None else None,
        "mtm_updated_at_ist": format_ist_display(str(mtm_updated)) if mtm_updated else None,
        "last_option_ltp": last_ltp,
        "current_option_ltp": current_option_ltp,
        "exit_option_ltp": exit_option_ltp if exit_option_ltp is not None else current_option_ltp if not is_open else None,
        "exit_index_price": exit_index_price,
        "mtm_history": mtm_history[-12:],
        "display_pnl": float(mtm_pnl) if is_open and mtm_pnl is not None else pnl,
        "entry_index_price": entry_price,
        "entry_strike": strike,
        "entry_option_ltp": entry_ltp,
        "quantity": qty,
        "configured_lot_size": leg.get("configured_lot_size"),
        "lot_label": leg.get("lot_label"),
        "segment": segment,
        "security_id": security_id,
        "signal_reason": signal.get("reason"),
        "cpr": {"pivot": signal.get("pivot"), "bc": signal.get("bc"), "tc": signal.get("tc")},
        "ema_fast": signal.get("ema_fast"),
        "ema_slow": signal.get("ema_slow"),
        "exit_label": exit_label,
        "is_paper": mode == "PAPER" or status == "PAPER_RECORDED",
        "structure": option.get("structure"),
        "legs_detail": option.get("legs") or [],
        "net_credit_points": option.get("net_credit_points"),
        "max_loss_rupees": option.get("max_loss_rupees"),
        "max_profit_rupees": option.get("max_profit_rupees"),
        "last_close_debit": option.get("last_close_debit"),
        "credit_risk_label": _credit_risk_label(option),
    }


def _credit_risk_label(option: dict[str, Any]) -> str | None:
    credit = option.get("net_credit_points")
    max_loss = option.get("max_loss_rupees")
    max_profit = option.get("max_profit_rupees")
    if credit is None and max_loss is None:
        return None
    parts: list[str] = []
    if credit is not None:
        parts.append(f"Credit ₹{float(credit):,.2f}/unit")
    if max_profit is not None:
        parts.append(f"max profit ₹{float(max_profit):,.0f}")
    if max_loss is not None:
        parts.append(f"max loss ₹{float(max_loss):,.0f}")
    return " · ".join(parts)


def today_trade_count() -> int:
    today = today_ist_date()
    with connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS total FROM trades WHERE substr(created_at, 1, 10) = ?",
            (today,),
        ).fetchone()
    return int(row["total"] if row else 0)


def today_losing_trades_count() -> int:
    """Closed trades today with negative recorded PnL."""
    today = today_ist_date()
    with connect() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total FROM trades
            WHERE substr(created_at, 1, 10) = ? AND pnl IS NOT NULL AND pnl < 0
            """,
            (today,),
        ).fetchone()
    return int(row["total"] if row else 0)


def open_trades() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM trades WHERE pnl IS NULL ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def update_trade_trail_meta(trade_id: str, meta: dict[str, Any]) -> None:
    with connect() as db:
        row = db.execute("SELECT option_json FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return
        option = json.loads(row["option_json"])
        option["trail_meta"] = meta
        db.execute(
            "UPDATE trades SET option_json = ? WHERE id = ?",
            (json.dumps(option, default=str), trade_id),
        )


def today_realized_pnl() -> float:
    today = today_ist_date()
    with connect() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE substr(created_at, 1, 10) = ?",
            (today,),
        ).fetchone()
    return float(row["total"] if row else 0.0)


def _is_test_trade_id(trade_id: str | None) -> bool:
    tid = str(trade_id or "").strip().lower()
    return tid.startswith("test-") or tid.startswith("test_") or tid == "real-trade-id"


def _is_excluded_feedback_note(note: str | None) -> bool:
    text = str(note or "").lower()
    return "unit test" in text or text.startswith("test ")


def _feedback_row_excluded(row: dict[str, Any]) -> bool:
    return _is_test_trade_id(row.get("trade_id")) or _is_excluded_feedback_note(row.get("note"))


def purge_test_learning_data() -> dict[str, int]:
    """Remove feedback/trades created by automated tests (does not touch real journal rows)."""
    with connect() as db:
        fb = db.execute(
            """
            DELETE FROM feedback
            WHERE trade_id LIKE 'test-%'
               OR trade_id = 'real-trade-id'
               OR lower(note) LIKE '%unit test%'
            """
        ).rowcount
        tr = db.execute("DELETE FROM trades WHERE id LIKE 'test-%'").rowcount
    update_learning()
    return {"feedback_removed": int(fb or 0), "trades_removed": int(tr or 0)}


def record_feedback(trade_id: str | None, rating: int, note: str | None = None) -> dict[str, Any]:
    clipped = max(-1, min(1, int(rating)))
    with connect() as db:
        db.execute(
            "INSERT INTO feedback (trade_id, rating, note, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, clipped, note, now_utc()),
        )
    return update_learning()


def record_trade_outcome(trade_id: str, pnl: float, note: str | None = None) -> dict[str, Any]:
    rating = 1 if pnl > 0 else -1 if pnl < 0 else 0
    with connect() as db:
        db.execute("UPDATE trades SET pnl = ? WHERE id = ?", (pnl, trade_id))
        db.execute(
            "INSERT INTO feedback (trade_id, rating, note, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, rating, note or f"Outcome PnL: {pnl}", now_utc()),
        )
    return update_learning()


def update_learning() -> dict[str, Any]:
    from index_ai.risk_policy import HARDCODED_RISK

    with connect() as db:
        fb_rows = db.execute(
            """
            SELECT trade_id, rating, note FROM feedback
            ORDER BY created_at DESC LIMIT 80
            """
        ).fetchall()
        closed_rows = db.execute(
            """
            SELECT id, pnl FROM trades
            WHERE pnl IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 80
            """
        ).fetchall()

    ratings = [
        int(r["rating"])
        for r in fb_rows
        if not _feedback_row_excluded(dict(r))
    ][:30]
    pnls = [
        float(r["pnl"])
        for r in closed_rows
        if not _is_test_trade_id(str(r["id"]))
    ][:50]
    avg = sum(ratings) / len(ratings) if ratings else 0.0
    min_confidence_adjustment = 0.0
    if ratings:
        if avg < -0.25:
            min_confidence_adjustment = 0.08
        elif avg > 0.25:
            min_confidence_adjustment = -0.03

    trade_win_rate = None
    if pnls:
        wins = sum(1 for p in pnls if p > 0)
        trade_win_rate = round(wins / len(pnls), 3)
        if len(pnls) >= 5:
            if trade_win_rate < 0.35:
                min_confidence_adjustment = max(min_confidence_adjustment, 0.10)
            elif trade_win_rate > 0.6:
                min_confidence_adjustment = min(min_confidence_adjustment, -0.04)

    base = HARDCODED_RISK.min_confidence
    effective = round(base + min_confidence_adjustment, 3)
    parts: list[str] = []
    if ratings:
        parts.append(f"{len(ratings)} feedback ratings (avg {avg:+.2f})")
    if pnls and trade_win_rate is not None:
        parts.append(f"{len(pnls)} closed trades (win rate {trade_win_rate:.0%})")
    elif pnls:
        parts.append(f"{len(pnls)} closed trades")
    if not parts:
        explanation = "No feedback or closed trades yet — using base confidence gate only."
    elif min_confidence_adjustment > 0:
        explanation = (
            f"Learning tightened gates: effective min confidence {effective:.0%} "
            f"(base {base:.0%} + {min_confidence_adjustment:.0%} from recent losses)."
        )
    elif min_confidence_adjustment < 0:
        explanation = (
            f"Learning relaxed gates slightly: effective min confidence {effective:.0%} "
            f"from strong recent results."
        )
    else:
        explanation = f"Neutral learning — effective min confidence stays {effective:.0%}."

    excluded_fb = sum(1 for r in fb_rows if _feedback_row_excluded(dict(r)))
    from index_ai.ml_outcomes import load_ml_status, train_outcome_model

    ml = train_outcome_model()
    ml_status = load_ml_status() if not ml.get("ready") else ml

    if ml.get("ready"):
        parts.append(
            f"ML model v{ml.get('version')} trained on {ml.get('training_samples')} trades "
            f"(holdout accuracy {(ml.get('holdout_accuracy') or 0):.0%}, "
            f"min win-prob gate {(ml.get('min_win_prob_gate') or 0):.0%})."
        )
        explanation = " ".join(parts) if parts else explanation
    elif ml.get("message") and pnls:
        parts.append(str(ml["message"]))
        explanation = " ".join(parts)

    from index_ai.hf_learning import load_hf_status, update_hf_learning

    hf = update_hf_learning()
    hf_status = load_hf_status()
    if hf.get("ready"):
        parts.append(
            f"Hugging Face ({hf.get('model')}): {hf.get('dataset_rows', 0)} outcomes in dataset."
        )
        explanation = " ".join(parts)
    elif hf.get("message"):
        parts.append(str(hf["message"]))

    learned = {
        "recent_feedback_count": len(ratings),
        "excluded_feedback_count": excluded_fb,
        "average_rating": round(avg, 3),
        "min_confidence_adjustment": round(min_confidence_adjustment, 3),
        "closed_trades_sample": len(pnls),
        "trade_win_rate": trade_win_rate,
        "base_min_confidence": base,
        "effective_min_confidence": effective,
        "ml_min_win_prob": float(ml_status.get("min_win_prob_gate") or 0.45),
        "learning_active": bool(ratings or pnls or ml.get("ready")),
        "explanation": explanation,
        "ml": ml_status,
        "hf": hf_status,
        "hf_min_positive_prob": float(os.getenv("HF_MIN_POSITIVE_PROB", "0.42")),
    }
    with connect() as db:
        db.execute(
            """
            INSERT INTO learned_settings (key, value_json, updated_at)
            VALUES ('strategy_feedback', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (json.dumps(learned), now_utc()),
        )
    return learned


def learned_settings() -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT value_json FROM learned_settings WHERE key = 'strategy_feedback'"
        ).fetchone()
    if not row:
        return update_learning()
    return json.loads(row["value_json"])


def learning_report() -> dict[str, Any]:
    """Full learning state for dashboard (recomputed from DB)."""
    learned = update_learning()
    with connect() as db:
        feedback_rows = db.execute(
            """
            SELECT trade_id, rating, note, created_at FROM feedback
            ORDER BY created_at DESC LIMIT 15
            """
        ).fetchall()
    feedback = []
    for r in feedback_rows:
        if _feedback_row_excluded(dict(r)):
            continue
        row = dict(r)
        row["created_at_ist"] = format_ist_display(str(row.get("created_at") or ""))
        note = str(row.get("note") or "")
        row["note_short"] = note.split(" @ ")[0].strip() if " @ " in note else note
        feedback.append(row)
        if len(feedback) >= 15:
            break
    return {
        "learned": learned,
        "timezone": "Asia/Kolkata",
        "recent_feedback": feedback,
    }
