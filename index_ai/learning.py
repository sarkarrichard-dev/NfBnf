from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from index_ai.config import DB_PATH, MEMORY_DIR
from index_ai.market_clock import format_ist_display, is_entry_session_timestamp, now_ist_iso, parse_ist_datetime, today_ist_date


def now_utc() -> str:
    """Backward-compatible alias — timestamps are stored in IST."""
    return now_ist_iso()


_schema_initialized = False


def init_db() -> None:
    global _schema_initialized
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.execute("PRAGMA journal_mode=WAL")
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
            CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at);
            CREATE INDEX IF NOT EXISTS idx_trades_instrument_action_created
                ON trades(instrument, action, created_at);
            """
        )
    _schema_initialized = True


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    if not _schema_initialized:
        init_db()
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
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


def infer_exit_ltp_from_pnl(
    option: dict[str, Any],
    pnl: float,
    *,
    qty: int | None = None,
) -> float | None:
    """Estimate exit premium from logged PnL when Dhan LTP was not saved."""
    entry = float(option.get("entry_ltp") or option.get("ltp") or 0)
    if entry <= 0:
        hist = option.get("mtm_history") or []
        if hist and hist[0].get("option_ltp") is not None:
            entry = float(hist[0]["option_ltp"])
    q = max(1, int(qty or option.get("quantity") or 1))
    if entry <= 0:
        return None
    tx = str(option.get("transaction_type") or "BUY").upper()
    legs = option.get("legs") or []
    if legs:
        return max(0.0, entry - float(pnl) / q)
    if tx == "BUY":
        return max(0.0, entry + float(pnl) / q)
    return max(0.0, entry - float(pnl) / q)


def backfill_option_prices_for_close(trade: dict[str, Any], pnl: float) -> dict[str, Any]:
    """Fill missing entry/exit LTP on option dict when closing from journal PnL only."""
    option = dict(trade.get("option") or {})
    _, effective_qty = resolve_trade_lot_size(trade)
    qty = effective_qty or int(option.get("quantity") or 1)
    entry = option.get("entry_ltp") or option.get("ltp")
    if entry is None:
        hist = option.get("mtm_history") or []
        if hist and hist[0].get("option_ltp") is not None:
            entry = float(hist[0]["option_ltp"])
            option["entry_ltp"] = entry
            option["ltp"] = entry
    if option.get("exit_ltp") is None and entry is not None:
        inferred = infer_exit_ltp_from_pnl(option, pnl, qty=qty)
        if inferred is not None:
            option["exit_ltp"] = round(inferred, 2)
            option["exit_inferred_from_pnl"] = True
    return option


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
    from index_ai.trade_lots import get_lots_per_trade

    option = trade.get("option") or {}
    inst_key = str(trade.get("instrument") or option.get("instrument") or "")
    stored = int(option.get("quantity") or 0)
    try:
        configured = int(get_instrument(inst_key).lot_size) * int(get_lots_per_trade())
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
    from index_ai.trade_lots import get_lots_per_trade

    lots = int(get_lots_per_trade())
    lot_label = f"{lots} lot{'s' if lots != 1 else ''} · {effective_qty} qty" if effective_qty else ""
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


def _normalize_option_type_label(raw: str) -> str:
    side = str(raw or "").upper()
    if side in ("CALL", "CE"):
        return "CE"
    if side in ("PUT", "PE"):
        return "PE"
    return side


def build_legs_ui(option: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-leg row for dashboard: strike, side, entry LTP, current LTP."""
    legs = list(option.get("legs") or [])
    leg_ltps = option.get("leg_ltps") or []
    broker_legs = (option.get("broker_orders") or {}).get("legs") or []
    rows: list[dict[str, Any]] = []
    for i, leg in enumerate(legs):
        tx = str(leg.get("transaction_type") or "BUY").upper()
        side = _normalize_option_type_label(str(leg.get("option_type") or ""))
        strike = leg.get("strike")
        if strike is not None and float(strike) == int(strike):
            strike_s = str(int(strike))
        elif strike is not None:
            strike_s = f"{float(strike):g}"
        else:
            strike_s = ""
        entry = leg.get("entry_ltp", leg.get("ltp"))
        current = leg.get("current_ltp")
        if current is None and i < len(leg_ltps):
            current = leg_ltps[i]
        exit_px = leg.get("exit_ltp")
        broker_id = leg.get("broker_order_id")
        if not broker_id and i < len(broker_legs):
            resp = (broker_legs[i].get("response") or {}) if isinstance(broker_legs[i], dict) else {}
            broker_id = resp.get("orderId")
        rows.append(
            {
                "transaction_type": tx,
                "option_type": side,
                "strike": strike,
                "strike_display": strike_s,
                "entry_ltp": float(entry) if entry is not None else None,
                "current_ltp": float(current) if current is not None else None,
                "exit_ltp": float(exit_px) if exit_px is not None else None,
                "quantity": int(leg.get("quantity") or option.get("quantity") or 1),
                "broker_order_id": str(broker_id) if broker_id else None,
                "label": f"{'Sell' if tx == 'SELL' else 'Buy'} {strike_s} {side}".strip(),
            }
        )
    return rows


def expand_ui_trade_to_leg_rows(ui: dict[str, Any]) -> list[dict[str, Any]]:
    """One UI row per option leg (spreads → separate buy/sell lines). Strategy stays off UI."""
    from index_ai.exit import estimate_pnl_rupees

    legs = list(ui.get("legs_detail") or [])
    if not legs:
        tx = str(ui.get("transaction_type") or "BUY").upper()
        legs = [
            {
                "transaction_type": tx,
                "option_type": _normalize_option_type_label(
                    str(ui.get("option_side") or ui.get("option_type") or "")
                ),
                "strike_display": ui.get("strike_display") or ui.get("entry_strike"),
                "entry_ltp": ui.get("entry_option_ltp"),
                "current_ltp": ui.get("current_option_ltp"),
                "exit_ltp": ui.get("exit_option_ltp"),
                "quantity": ui.get("quantity"),
                "broker_order_id": (ui.get("broker_order_ids") or [None])[0],
            }
        ]

    is_open = bool(ui.get("is_open"))
    trade_pnl = ui.get("pnl")
    spread_mtm = ui.get("mtm_pnl")
    status = str(ui.get("status") or "")
    rejected = status == "LIVE_REJECTED"
    awaiting = status in {"LIVE_SENT", "LIVE_PENDING"}
    if rejected:
        is_open = False
        trade_pnl = None
        spread_mtm = None
    mode_label = "Live" if ui.get("is_live") else "Paper"
    if ui.get("is_paper"):
        mode_label = "Paper"

    out: list[dict[str, Any]] = []
    leg_count = len(legs)
    for idx, leg in enumerate(legs):
        tx = str(leg.get("transaction_type") or "BUY").upper()
        side_word = "Sell" if tx == "SELL" else "Buy"
        qty = int(leg.get("quantity") or ui.get("quantity") or 1)
        entry = None if rejected else leg.get("entry_ltp")
        mark = None if rejected else (leg.get("current_ltp") if is_open else (leg.get("exit_ltp") or leg.get("current_ltp")))
        leg_mtm = None
        leg_pnl = None
        if not rejected and entry is not None and mark is not None and is_open and not awaiting:
            leg_mtm = estimate_pnl_rupees(
                entry_ltp=float(entry),
                exit_ltp=float(mark),
                quantity=qty,
                transaction_type=tx,
            )
        elif not rejected and entry is not None and not is_open:
            exit_px = leg.get("exit_ltp") or mark
            if exit_px is not None:
                leg_pnl = estimate_pnl_rupees(
                    entry_ltp=float(entry),
                    exit_ltp=float(exit_px),
                    quantity=qty,
                    transaction_type=tx,
                )

        show_spread_pnl = (
            not rejected
            and idx == 0
            and trade_pnl is not None
            and leg_pnl is None
            and float(trade_pnl) != 0.0
        )
        display_pnl = None
        if awaiting:
            display_pnl = None
        elif is_open and leg_mtm is not None:
            display_pnl = leg_mtm
        elif leg_pnl is not None:
            display_pnl = leg_pnl
        elif show_spread_pnl:
            display_pnl = float(trade_pnl)
        elif idx == 0 and is_open and spread_mtm is not None:
            display_pnl = float(spread_mtm)

        out.append(
            {
                "trade_id": ui.get("id"),
                "leg_index": idx,
                "leg_count": leg_count,
                "open_time_ist": ui.get("created_at_ist"),
                "close_time_ist": (
                    ui.get("closed_at_ist") or ui.get("created_at_ist")
                    if rejected or not is_open
                    else None
                ),
                "instrument": ui.get("instrument"),
                "side": side_word,
                "strike": leg.get("strike_display") or leg.get("strike"),
                "option_type": _normalize_option_type_label(str(leg.get("option_type") or "")),
                "quantity": qty,
                "avg_entry": entry,
                "avg_exit": leg.get("exit_ltp") if not is_open else None,
                "mark_price": mark if is_open else leg.get("exit_ltp"),
                "leg_mtm": leg_mtm,
                "leg_pnl": leg_pnl,
                "spread_pnl": float(trade_pnl) if show_spread_pnl else None,
                "display_pnl": display_pnl,
                "is_open": is_open,
                "status": status,
                "display_status": ui.get("display_status"),
                "mode": mode_label,
                "is_live": ui.get("is_live"),
                "expiry": ui.get("expiry"),
                "broker_order_id": leg.get("broker_order_id"),
                "broker_status_line": ui.get("broker_status_line") if idx == 0 else None,
                "mtm_updated_at_ist": ui.get("mtm_updated_at_ist") if is_open else None,
                "entry_session_ok": ui.get("entry_session_ok"),
                "row_class": (
                    "row-open"
                    if is_open
                    else "row-rejected"
                    if status == "LIVE_REJECTED"
                    else ""
                ),
                "leg_group_class": "leg-group-start" if idx == 0 else "leg-group-cont",
            }
        )
    return out


def expand_trades_to_log_rows(ui_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ui in ui_trades:
        rows.extend(expand_ui_trade_to_leg_rows(ui))
    return rows


def repair_rejected_journal_prices(*, limit: int = 200) -> int:
    """One-time cleanup: strip phantom premiums from LIVE_REJECTED rows in SQLite."""
    updated = 0
    with connect() as db:
        rows = db.execute(
            """
            SELECT id, option_json FROM trades
            WHERE status = 'LIVE_REJECTED'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    for row in rows:
        tid = str(row["id"])
        if _is_test_trade_id(tid):
            continue
        opt = json.loads(row["option_json"])
        dirty = any(
            opt.get(k) is not None
            for k in ("entry_ltp", "ltp", "mtm_pnl", "net_credit_points")
        ) or any(
            isinstance(leg, dict) and leg.get("entry_ltp") is not None
            for leg in (opt.get("legs") or [])
        )
        if not dirty:
            continue
        clean = sanitize_rejected_option(opt)
        with connect() as db:
            db.execute(
                "UPDATE trades SET option_json = ? WHERE id = ?",
                (json.dumps(clean, default=str), tid),
            )
        updated += 1
    return updated


def format_trade_for_ui(trade: dict[str, Any]) -> dict[str, Any]:
    """Flatten signal/option into dashboard-friendly entry/exit fields."""
    trade = sync_option_lot_size(trade)
    signal = trade.get("signal") or {}
    option = trade.get("option") or {}
    action = str(trade.get("action") or signal.get("action") or "")
    leg = option_leg_fields(trade)
    pnl = trade.get("pnl")
    mode = str(trade.get("mode") or "")
    status = str(trade.get("status") or "")
    if status == "LIVE_REJECTED":
        option = sanitize_rejected_option(option)
    is_open = pnl is None and (
        not is_live_trade(trade) or is_broker_filled_open(trade)
    )
    _, effective_qty = resolve_trade_lot_size(trade)
    qty = effective_qty or int(option.get("quantity") or 1)
    entry_price = signal.get("price")
    strike = option.get("strike")
    entry_ltp = None if status == "LIVE_REJECTED" else (option.get("entry_ltp") or option.get("ltp"))
    if entry_ltp is None and status != "LIVE_REJECTED":
        hist = option.get("mtm_history") or []
        if hist and hist[0].get("option_ltp") is not None:
            entry_ltp = float(hist[0]["option_ltp"])
    if (
        pnl is not None
        and status != "LIVE_REJECTED"
        and option.get("exit_ltp") is None
        and entry_ltp is not None
    ):
        inferred_exit = infer_exit_ltp_from_pnl(option, float(pnl), qty=qty)
        if inferred_exit is not None:
            option = {**option, "exit_ltp": round(inferred_exit, 2), "exit_inferred_from_pnl": True}
    exit_option_ltp = option.get("exit_ltp")
    exit_inferred_from_pnl = bool(option.get("exit_inferred_from_pnl"))
    prices_incomplete = (
        pnl is not None and entry_ltp is None and exit_option_ltp is None
    )
    segment = option.get("segment") or ""
    security_id = option.get("security_id")
    exit_index_price = option.get("exit_index_price")
    awaiting_broker = status in {"LIVE_SENT", "LIVE_PENDING"}
    mtm_pnl = None if awaiting_broker else option.get("mtm_pnl")
    last_ltp = None if awaiting_broker else option.get("last_option_ltp")
    mtm_updated = None if awaiting_broker else option.get("mtm_updated_at")
    mtm_history = [] if awaiting_broker else (option.get("mtm_history") or [])
    current_option_ltp = resolve_current_option_ltp(
        option,
        is_open=is_open,
        mtm_pnl=float(mtm_pnl) if mtm_pnl is not None else None,
        entry_ltp=float(entry_ltp) if entry_ltp is not None else None,
        qty=qty,
        tx=leg["transaction_type"],
    )

    broker_status = str(status or "").upper()
    broker_poll = option.get("broker_order_poll") or []
    broker_status_line = None
    if broker_poll:
        broker_status_line = "; ".join(
            f"{p.get('order_id')}: {p.get('status')}"
            + (f" ({p.get('detail')})" if p.get("detail") else "")
            for p in broker_poll
        )
    elif option.get("broker_order_statuses"):
        broker_status_line = ", ".join(str(s) for s in option["broker_order_statuses"])

    if broker_status == "LIVE_REJECTED":
        exit_label = option.get("broker_rejection_reason") or broker_status_line or "Rejected on Dhan"
    elif pnl is not None and float(pnl) != 0:
        if exit_option_ltp is not None:
            est = " (est. from PnL)" if exit_inferred_from_pnl else ""
            exit_label = f"Closed @ ₹{float(exit_option_ltp):,.2f}{est}"
        else:
            exit_label = f"Closed (PnL ₹{float(pnl):,.2f} — premium not recorded)"
    elif awaiting_broker:
        exit_label = broker_status_line or "Waiting for Dhan order status"
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
    closed_at = option.get("closed_at")
    created_dt = parse_ist_datetime(str(created) if created else None)
    entry_session_ok = bool(created_dt and is_entry_session_timestamp(created_dt))
    return {
        "id": trade.get("id"),
        "created_at": created,
        "created_at_ist": format_ist_display(str(created) if created else None),
        "entry_session_ok": entry_session_ok,
        "closed_at": closed_at,
        "closed_at_ist": format_ist_display(str(closed_at)) if closed_at else None,
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
        "display_status": (
            "Rejected · Dhan"
            if broker_status == "LIVE_REJECTED"
            else (
                "Closed"
                if pnl is not None
                else (
                    "Paper · open"
                    if mode == "PAPER" or status == "PAPER_RECORDED"
                    else (
                        "Live · filled"
                        if status == "LIVE_TRADED"
                        else (
                            "Live · awaiting Dhan"
                            if status in {"LIVE_SENT", "LIVE_PENDING"}
                            else (status or "Open")
                        )
                    )
                )
            )
        ),
        "broker_status_line": broker_status_line,
        "broker_rejection_reason": option.get("broker_rejection_reason"),
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
        "is_live": mode == "LIVE" or str(status).upper().startswith("LIVE"),
        "broker_order_ids": option.get("broker_order_ids") or [],
        "broker_orders": option.get("broker_orders"),
        "structure": option.get("structure"),
        "legs_detail": build_legs_ui(option),
        "net_credit_points": option.get("net_credit_points"),
        "mtm_error": option.get("mtm_error"),
        "max_loss_rupees": option.get("max_loss_rupees"),
        "max_profit_rupees": option.get("max_profit_rupees"),
        "last_close_debit": option.get("last_close_debit"),
        "credit_risk_label": _credit_risk_label(option),
        "exit_inferred_from_pnl": exit_inferred_from_pnl,
        "prices_incomplete": prices_incomplete,
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
    """Closed trades today with negative recorded PnL (total count, not streak)."""
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


def today_consecutive_loss_streak() -> int:
    """Current streak of consecutive closed losses today (all modes)."""
    return _today_consecutive_loss_streak(live_only=False)


def today_live_consecutive_loss_streak() -> int:
    """Consecutive closed losses today for live broker journal rows only."""
    return _today_consecutive_loss_streak(live_only=True)


def _today_consecutive_loss_streak(*, live_only: bool) -> int:
    today = today_ist_date()
    with connect() as db:
        rows = db.execute(
            """
            SELECT mode, status, pnl FROM trades
            WHERE substr(created_at, 1, 10) = ? AND pnl IS NOT NULL
            ORDER BY created_at ASC
            """,
            (today,),
        ).fetchall()
    streak = 0
    for row in rows:
        trade = {"mode": row["mode"], "status": row["status"]}
        if live_only and not is_live_trade(trade):
            continue
        if float(row["pnl"]) < 0:
            streak += 1
        else:
            streak = 0
    return streak


def is_live_trade(trade: dict[str, Any]) -> bool:
    """True when the row was recorded as a live broker journal entry."""
    mode = str(trade.get("mode") or "").upper()
    status = str(trade.get("status") or "").upper()
    return mode == "LIVE" or status.startswith("LIVE")


def is_broker_filled_open(trade: dict[str, Any]) -> bool:
    """Live row with confirmed fills on Dhan — safe for trails and MTM."""
    if not is_live_trade(trade):
        return True
    return str(trade.get("status") or "").upper() == "LIVE_TRADED"


def live_trades_for_broker_sync() -> list[dict[str, Any]]:
    """Live journal rows needing Dhan sync (all open live rows + today's false rejects)."""
    today = today_ist_date()
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM trades
            WHERE (upper(mode) = 'LIVE' OR upper(status) LIKE 'LIVE%')
              AND (
                (pnl IS NULL AND upper(status) IN ('LIVE_SENT', 'LIVE_PENDING', 'LIVE_TRADED'))
                OR (status = 'LIVE_REJECTED' AND pnl = 0 AND substr(created_at, 1, 10) = ?)
              )
            ORDER BY created_at DESC
            """,
            (today,),
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def open_trades() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM trades
            WHERE pnl IS NULL
              AND upper(status) NOT IN ('LIVE_REJECTED', 'LIVE_FAILED')
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


def persist_trade_option_fields(trade_id: str, option: dict[str, Any]) -> None:
    with connect() as db:
        db.execute(
            "UPDATE trades SET option_json = ? WHERE id = ?",
            (json.dumps(option, default=str), trade_id),
        )


def update_trade_status(
    trade_id: str,
    status: str,
    *,
    option: dict[str, Any] | None = None,
) -> None:
    with connect() as db:
        if option is not None:
            db.execute(
                "UPDATE trades SET status = ?, option_json = ? WHERE id = ?",
                (status, json.dumps(option, default=str), trade_id),
            )
        else:
            db.execute("UPDATE trades SET status = ? WHERE id = ?", (status, trade_id))


def clear_option_mtm_fields(option: dict[str, Any]) -> dict[str, Any]:
    """Remove stale MTM so LIVE_SENT / rejected rows are not shown as open PnL."""
    opt = dict(option)
    for key in (
        "mtm_pnl",
        "mtm_updated_at",
        "mtm_error",
        "mtm_history",
        "last_option_ltp",
        "last_close_debit",
        "trail_meta",
    ):
        opt.pop(key, None)
    legs = opt.get("legs")
    if isinstance(legs, list):
        cleaned: list[dict[str, Any]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            row = dict(leg)
            row.pop("current_ltp", None)
            cleaned.append(row)
        opt["legs"] = cleaned
    return opt


def sanitize_rejected_option(option: dict[str, Any]) -> dict[str, Any]:
    """Strip all premiums/PnL hints — rejected orders never filled."""
    opt = clear_option_mtm_fields(dict(option))
    for key in (
        "entry_ltp",
        "ltp",
        "exit_ltp",
        "exit_inferred_from_pnl",
        "net_credit_points",
        "leg_ltps",
        "last_option_ltp",
    ):
        opt.pop(key, None)
    legs = opt.get("legs")
    if isinstance(legs, list):
        cleaned: list[dict[str, Any]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            row = dict(leg)
            for key in ("entry_ltp", "ltp", "exit_ltp", "current_ltp"):
                row.pop(key, None)
            cleaned.append(row)
        opt["legs"] = cleaned
    if not opt.get("closed_at"):
        opt["closed_at"] = now_ist_iso()
    return opt


def reopen_live_traded_trade(
    trade_id: str,
    *,
    option: dict[str, Any] | None = None,
) -> None:
    """Dhan confirms fills after a false reject — restore open live position."""
    with connect() as db:
        row = db.execute("SELECT option_json FROM trades WHERE id = ?", (trade_id,)).fetchone()
        opt = dict(option or {})
        if not opt and row:
            opt = json.loads(row["option_json"])
        opt.pop("broker_rejection_reason", None)
        db.execute(
            "UPDATE trades SET pnl = NULL, status = ?, option_json = ? WHERE id = ?",
            ("LIVE_TRADED", json.dumps(opt, default=str), trade_id),
        )


def reject_live_trade(
    trade_id: str,
    reason: str,
    *,
    option: dict[str, Any] | None = None,
) -> None:
    """Broker/exchange rejected — close journal row with zero PnL (not an open position)."""
    with connect() as db:
        row = db.execute("SELECT option_json FROM trades WHERE id = ?", (trade_id,)).fetchone()
        opt = option
        if opt is None and row:
            opt = json.loads(row["option_json"])
        opt = sanitize_rejected_option(dict(opt or {}))
        opt["broker_rejection_reason"] = str(reason)[:500]
        db.execute(
            "UPDATE trades SET pnl = 0, status = ?, option_json = ? WHERE id = ?",
            ("LIVE_REJECTED", json.dumps(opt, default=str), trade_id),
        )


def open_trades_for_mode(mode: str) -> list[dict[str, Any]]:
    """Open positions for the active execution mode only (paper vs live)."""
    normalized = str(mode or "PAPER").strip().upper()
    all_open = open_trades()
    if normalized == "LIVE":
        return [t for t in all_open if is_live_trade(t)]
    return [t for t in all_open if not is_live_trade(t)]


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
    """Realized PnL today across paper and live journal rows."""
    return _today_realized_pnl(live_only=False)


def today_live_realized_pnl() -> float:
    """Realized PnL today for live broker journal rows only (kill switch)."""
    return _today_realized_pnl(live_only=True)


def _today_realized_pnl(*, live_only: bool) -> float:
    today = today_ist_date()
    with connect() as db:
        rows = db.execute(
            "SELECT mode, status, pnl FROM trades WHERE substr(created_at, 1, 10) = ? AND pnl IS NOT NULL",
            (today,),
        ).fetchall()
    total = 0.0
    for row in rows:
        trade = {"mode": row["mode"], "status": row["status"]}
        if live_only and not is_live_trade(trade):
            continue
        total += float(row["pnl"] or 0)
    return total


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def setup_loss_profile(*, limit: int = 120) -> dict[str, Any]:
    """Recent closed-trade performance by index and action for loss-aware gating."""
    with connect() as db:
        rows = db.execute(
            """
            SELECT id, created_at, instrument, action, pnl
            FROM trades
            WHERE pnl IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()

    by_setup: dict[str, dict[str, Any]] = {}
    by_index: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row["id"] or "")
        if _is_test_trade_id(tid):
            continue
        instrument = str(row["instrument"] or "").upper() or "UNKNOWN"
        action = str(row["action"] or "").upper() or "UNKNOWN"
        pnl = float(row["pnl"] or 0.0)
        for key, bucket in (
            (f"{instrument}:{action}", by_setup),
            (instrument, by_index),
        ):
            stats = bucket.setdefault(
                key,
                {
                    "instrument": instrument,
                    "action": action if ":" in key else None,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0.0,
                    "last_pnls": [],
                },
            )
            stats["trades"] += 1
            stats["wins"] += 1 if pnl > 0 else 0
            stats["losses"] += 1 if pnl < 0 else 0
            stats["pnl"] += pnl
            stats["last_pnls"].append(round(pnl, 2))

    def finalize(items: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, stats in items.items():
            trades = int(stats["trades"])
            losses = int(stats["losses"])
            out[key] = {
                **stats,
                "pnl": round(float(stats["pnl"]), 2),
                "loss_rate": round(losses / trades, 3) if trades else 0.0,
                "win_rate": round(int(stats["wins"]) / trades, 3) if trades else 0.0,
            }
        return out

    return {
        "by_setup": finalize(by_setup),
        "by_index": finalize(by_index),
    }


def loss_guard_for_setup(instrument: str, action: str) -> dict[str, Any]:
    """Block repeated weak setups when recent closed trades for that setup are poor."""
    if not _env_bool("LOSS_GUARD_ENABLED", True):
        return {"blocked": False, "reason": "Loss guard disabled."}
    min_trades = max(1, _env_int("LOSS_GUARD_MIN_TRADES", 3))
    loss_rate_gate = min(1.0, max(0.0, _env_float("LOSS_GUARD_MAX_LOSS_RATE", 0.67)))
    streak_gate = max(1, _env_int("LOSS_GUARD_CONSECUTIVE_LOSSES", 2))
    profile = setup_loss_profile(limit=_env_int("LOSS_GUARD_LOOKBACK_TRADES", 120))
    key = f"{instrument.upper()}:{action.upper()}"
    stats = profile["by_setup"].get(key)
    if not stats:
        return {"blocked": False, "reason": "No closed-trade history for this setup yet."}

    recent = [float(v) for v in stats.get("last_pnls") or []]
    streak = 0
    for pnl in recent:
        if pnl < 0:
            streak += 1
        else:
            break
    trades = int(stats["trades"])
    losses = int(stats["losses"])
    exact_loss_rate = losses / trades if trades else 0.0
    bad_sample = (
        trades >= min_trades
        and round(exact_loss_rate, 2) >= loss_rate_gate
        and float(stats["pnl"]) < 0
    )
    bad_streak = streak >= streak_gate
    blocked = bad_sample or bad_streak
    reason = "Loss guard passed."
    if blocked:
        reason = (
            f"Loss guard blocked {instrument.upper()} {action.upper()}: "
            f"{stats['losses']}/{stats['trades']} recent closed trades lost "
            f"(loss rate {float(stats['loss_rate']):.0%}, PnL â‚¹{float(stats['pnl']):,.0f}, "
            f"current loss streak {streak})."
        )
    return {
        "blocked": blocked,
        "reason": reason,
        "stats": stats,
        "current_loss_streak": streak,
        "thresholds": {
            "min_trades": min_trades,
            "loss_rate": loss_rate_gate,
            "consecutive_losses": streak_gate,
        },
    }


def _is_test_trade_id(trade_id: str | None) -> bool:
    tid = str(trade_id or "").strip().lower()
    return tid.startswith("test-") or tid.startswith("test_")


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


def repair_closed_trade_prices(*, limit: int = 200) -> int:
    """One-time style repair: infer missing entry/exit LTP on closed journal rows."""
    updated = 0
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    for row in rows:
        trade = _row_to_trade(row)
        tid = str(trade.get("id") or "")
        if not tid or _is_test_trade_id(tid):
            continue
        option = dict(trade.get("option") or {})
        has_entry = option.get("entry_ltp") or option.get("ltp") or (
            (option.get("mtm_history") or [{}])[0].get("option_ltp")
        )
        has_exit = option.get("exit_ltp")
        if has_entry and has_exit:
            continue
        new_option = backfill_option_prices_for_close(trade, float(trade.get("pnl") or 0))
        if new_option == option:
            continue
        with connect() as db:
            db.execute(
                "UPDATE trades SET option_json = ? WHERE id = ?",
                (json.dumps(new_option, default=str), tid),
            )
        updated += 1
    return updated


def record_trade_outcome(trade_id: str, pnl: float, note: str | None = None) -> dict[str, Any]:
    rating = 1 if pnl > 0 else -1 if pnl < 0 else 0
    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row:
            trade = _row_to_trade(row)
            option = backfill_option_prices_for_close(trade, float(pnl))
            if not option.get("closed_at"):
                option["closed_at"] = now_ist_iso()
            cur = db.execute(
                "UPDATE trades SET pnl = ?, status = ?, option_json = ? WHERE id = ? AND pnl IS NULL",
                (pnl, "CLOSED", json.dumps(option, default=str), trade_id),
            )
        else:
            cur = db.execute(
                "UPDATE trades SET pnl = ?, status = ? WHERE id = ? AND pnl IS NULL",
                (pnl, "CLOSED", trade_id),
            )
        if cur.rowcount == 0:
            db.execute(
                "INSERT INTO feedback (trade_id, rating, note, created_at) VALUES (?, ?, ?, ?)",
                (trade_id, rating, note or f"Outcome PnL: {pnl}", now_utc()),
            )
        else:
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
    from index_ai.oi_learning import analyze_oi_outcomes

    oi_insights = analyze_oi_outcomes()
    if oi_insights.get("recommendations"):
        parts.append(" ".join(oi_insights["recommendations"][:2]))
    elif oi_insights.get("message"):
        parts.append(str(oi_insights["message"]))

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
        "oi_insights": oi_insights,
        "loss_profile": setup_loss_profile(),
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
