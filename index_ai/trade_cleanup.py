"""Detect and remove duplicate or low-quality paper journal trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from index_ai.credit_spread import credit_spread_entry_ready, is_credit_action
from index_ai.learning import (
    _is_test_trade_id,
    _row_to_trade,
    connect,
    format_trade_for_ui,
    update_learning,
)
from index_ai.market_clock import format_ist_display, parse_ist_datetime


DUPLICATE_WINDOW_MINUTES = 120


@dataclass(frozen=True)
class CleanupCandidate:
    trade_id: str
    reason: str
    detail: str
    keep: bool
    group_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "reason": self.reason,
            "detail": self.detail,
            "keep": self.keep,
            "group_key": self.group_key,
        }


def _trade_quality_score(trade: dict[str, Any]) -> int:
    """Higher = richer journal row; prefer keeping when deduplicating."""
    option = trade.get("option") or {}
    signal = trade.get("signal") or {}
    score = 0
    if option.get("strike") is not None:
        score += 5
    if option.get("security_id"):
        score += 4
    if option.get("ltp") or option.get("entry_ltp"):
        score += 4
    if option.get("legs"):
        score += 10
    if option.get("expiry"):
        score += 2
    if signal.get("reason"):
        score += 1
    if trade.get("pnl") is not None:
        score += 1
    return score


def _is_incomplete_credit_spread(trade: dict[str, Any]) -> bool:
    action = str(trade.get("action") or "")
    if not is_credit_action(action):
        return False
    option = trade.get("option") or {}
    ok, _ = credit_spread_entry_ready(option, action=action)
    return not ok


def _is_incomplete_directional(trade: dict[str, Any]) -> bool:
    """Buy call/put rows with no strike, no chain leg, and no entry premium."""
    action = str(trade.get("action") or "")
    if action not in {"BUY_CALL", "BUY_PUT"}:
        return False
    option = trade.get("option") or {}
    if option.get("legs"):
        return False
    if option.get("strike") is not None or option.get("security_id"):
        return False
    if option.get("ltp") or option.get("entry_ltp"):
        return False
    hist = option.get("mtm_history") or []
    if hist and hist[0].get("option_ltp") is not None:
        return False
    return True


def _duplicate_group_key(trade: dict[str, Any]) -> str:
    signal = trade.get("signal") or {}
    pnl = trade.get("pnl")
    pnl_key = round(float(pnl), 2) if pnl is not None else "open"
    price = round(float(signal.get("price") or 0))
    return "|".join(
        [
            str(trade.get("instrument") or ""),
            str(trade.get("action") or ""),
            str(pnl_key),
            str(price),
        ]
    )


def _cluster_by_time(
    trades: list[dict[str, Any]], *, window: timedelta
) -> list[list[dict[str, Any]]]:
    if not trades:
        return []
    sorted_rows = sorted(
        trades,
        key=lambda t: parse_ist_datetime(str(t.get("created_at") or "")) or t.get("created_at"),
    )
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [sorted_rows[0]]
    for row in sorted_rows[1:]:
        prev_ts = parse_ist_datetime(str(current[-1].get("created_at") or ""))
        cur_ts = parse_ist_datetime(str(row.get("created_at") or ""))
        if prev_ts and cur_ts and (cur_ts - prev_ts) <= window:
            current.append(row)
        else:
            clusters.append(current)
            current = [row]
    clusters.append(current)
    return clusters


def scan_trade_cleanup(*, limit: int = 500) -> dict[str, Any]:
    """Find test ids, incomplete junk, and near-duplicate journal rows."""
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    trades = [_row_to_trade(r) for r in rows]
    candidates: list[CleanupCandidate] = []
    window = timedelta(minutes=DUPLICATE_WINDOW_MINUTES)

    for trade in trades:
        tid = str(trade.get("id") or "")
        if _is_test_trade_id(tid):
            candidates.append(
                CleanupCandidate(
                    trade_id=tid,
                    reason="test",
                    detail="Automated test trade id",
                    keep=False,
                    group_key=f"test:{tid}",
                )
            )

    by_key: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        tid = str(trade.get("id") or "")
        if _is_test_trade_id(tid):
            continue
        if _is_incomplete_directional(trade):
            candidates.append(
                CleanupCandidate(
                    trade_id=tid,
                    reason="incomplete",
                    detail="Buy option without strike, security_id, or entry premium",
                    keep=False,
                    group_key=f"incomplete:{tid}",
                )
            )
        elif _is_incomplete_credit_spread(trade):
            candidates.append(
                CleanupCandidate(
                    trade_id=tid,
                    reason="incomplete",
                    detail="Credit spread missing full option legs (likely test or partial snapshot)",
                    keep=False,
                    group_key=f"incomplete_credit:{tid}",
                )
            )
        key = _duplicate_group_key(trade)
        by_key.setdefault(key, []).append(trade)

    for key, group in by_key.items():
        if len(group) < 2:
            continue
        for cluster in _cluster_by_time(group, window=window):
            if len(cluster) < 2:
                continue
            ranked = sorted(
                cluster,
                key=lambda t: (
                    _trade_quality_score(t),
                    str(t.get("created_at") or ""),
                ),
                reverse=True,
            )
            keeper_id = str(ranked[0].get("id") or "")
            for trade in ranked[1:]:
                tid = str(trade.get("id") or "")
                candidates.append(
                    CleanupCandidate(
                        trade_id=tid,
                        reason="duplicate",
                        detail=f"Duplicate of {keeper_id} (same index/signal/PnL within {DUPLICATE_WINDOW_MINUTES}m)",
                        keep=False,
                        group_key=f"dup:{key}",
                    )
                )

    remove_ids = {c.trade_id for c in candidates if not c.keep}
    preview_rows = []
    for trade in trades:
        tid = str(trade.get("id") or "")
        if tid not in remove_ids:
            continue
        ui = format_trade_for_ui(trade)
        cand = next((c for c in candidates if c.trade_id == tid), None)
        preview_rows.append(
            {
                "trade_id": tid,
                "reason": cand.reason if cand else "unknown",
                "detail": cand.detail if cand else "",
                "created_at_ist": ui.get("created_at_ist"),
                "instrument": ui.get("instrument"),
                "action": ui.get("action"),
                "pnl": ui.get("pnl"),
                "leg_display": ui.get("leg_display"),
            }
        )

    by_reason: dict[str, int] = {}
    for c in candidates:
        if c.keep:
            continue
        by_reason[c.reason] = by_reason.get(c.reason, 0) + 1

    return {
        "remove_count": len(remove_ids),
        "remove_ids": sorted(remove_ids),
        "by_reason": by_reason,
        "candidates": [c.to_dict() for c in candidates if not c.keep],
        "preview": preview_rows,
        "window_minutes": DUPLICATE_WINDOW_MINUTES,
        "message": (
            f"Found {len(remove_ids)} trade(s) to remove "
            f"({by_reason.get('duplicate', 0)} duplicate, "
            f"{by_reason.get('incomplete', 0)} incomplete, "
            f"{by_reason.get('test', 0)} test)."
            if remove_ids
            else "No duplicate or junk trades found."
        ),
    }


def remove_trades(
    trade_ids: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete trades and linked feedback; recompute learning."""
    ids = [str(t).strip() for t in trade_ids if str(t).strip()]
    if not ids:
        return {"trades_removed": 0, "feedback_removed": 0, "dry_run": dry_run}

    if dry_run:
        return {
            "trades_removed": len(ids),
            "feedback_removed": 0,
            "dry_run": True,
            "trade_ids": ids,
        }

    placeholders = ",".join("?" for _ in ids)
    with connect() as db:
        fb = db.execute(
            f"DELETE FROM feedback WHERE trade_id IN ({placeholders})",
            ids,
        ).rowcount
        tr = db.execute(
            f"DELETE FROM trades WHERE id IN ({placeholders})",
            ids,
        ).rowcount
    update_learning()
    return {
        "trades_removed": int(tr or 0),
        "feedback_removed": int(fb or 0),
        "dry_run": False,
        "trade_ids": ids,
    }


def run_trade_cleanup(
    *,
    trade_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Preview scan or delete flagged / explicit trade ids."""
    if trade_ids:
        return {
            "scan": scan_trade_cleanup(),
            "removed": remove_trades(trade_ids, dry_run=dry_run),
        }
    scan = scan_trade_cleanup()
    if dry_run:
        return {"scan": scan, "removed": remove_trades(scan["remove_ids"], dry_run=True)}
    removed = remove_trades(scan["remove_ids"], dry_run=False)
    return {"scan": scan, "removed": removed, "learning": update_learning()}
