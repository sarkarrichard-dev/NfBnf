"""
Broker-vs-journal reconciliation.

``sync_trade_broker_status`` already refreshes each order's status. What was
missing is the reverse check: does the *net position at the broker* actually
match what the journal thinks is open? Drift happens for real reasons — a
partial fill, a manual square-off in the Dhan app, an order that filled after we
gave up polling, a leg rejected while its hedge went through.

This detects four drift classes and reports them. It is **read-only by default**:
auto-repair only ever touches the journal (our own bookkeeping), never places or
cancels a broker order — an automated system that reacts to a confusing broker
state by firing orders is how small problems become large ones.

    UNDER_FILLED    journal says N lots, broker holds fewer
    OVER_FILLED     broker holds more than the journal expects
    ORPHAN_BROKER   broker holds a position with no open journal row
    GHOST_JOURNAL   journal row open, broker holds nothing
"""

from __future__ import annotations

import os
from typing import Any

from index_ai.market_clock import now_ist_iso

UNDER_FILLED = "UNDER_FILLED"
OVER_FILLED = "OVER_FILLED"
ORPHAN_BROKER = "ORPHAN_BROKER"
GHOST_JOURNAL = "GHOST_JOURNAL"


def auto_repair_enabled() -> bool:
    """Journal-only repair of GHOST_JOURNAL rows. Never touches broker orders."""
    return os.getenv("RECONCILE_AUTO_REPAIR", "false").strip().lower() in {"1", "true", "yes", "on"}


def _journal_legs(trade: dict[str, Any]) -> list[dict[str, Any]]:
    option = dict(trade.get("option") or {})
    legs = list(option.get("legs") or [])
    if legs:
        return legs
    if option.get("security_id") is not None:
        return [option]
    return []


def _signed_qty(leg: dict[str, Any]) -> int:
    qty = int(leg.get("quantity") or 0)
    return -qty if str(leg.get("transaction_type") or "BUY").upper() == "SELL" else qty


def expected_positions(trades: list[dict[str, Any]]) -> dict[int, int]:
    """security_id -> signed qty the journal believes is open."""
    out: dict[int, int] = {}
    for t in trades:
        for leg in _journal_legs(t):
            sid = leg.get("security_id")
            if sid is None:
                continue
            out[int(sid)] = out.get(int(sid), 0) + _signed_qty(leg)
    return {k: v for k, v in out.items() if v != 0}


def diff_positions(
    expected: dict[int, int], actual: dict[int, int]
) -> list[dict[str, Any]]:
    """Classify every security_id where journal and broker disagree."""
    issues: list[dict[str, Any]] = []
    for sid in sorted(set(expected) | set(actual)):
        exp, act = expected.get(sid, 0), actual.get(sid, 0)
        if exp == act:
            continue
        if exp == 0:
            kind = ORPHAN_BROKER
        elif act == 0:
            kind = GHOST_JOURNAL
        elif abs(act) < abs(exp) and (act == 0 or (act > 0) == (exp > 0)):
            kind = UNDER_FILLED
        elif abs(act) > abs(exp) and (exp > 0) == (act > 0):
            kind = OVER_FILLED
        else:
            kind = ORPHAN_BROKER  # sign flip — broker is on the other side entirely
        issues.append({
            "security_id": sid,
            "kind": kind,
            "expected_qty": exp,
            "broker_qty": act,
            "delta": act - exp,
        })
    return issues


def reconcile(client: Any, *, mode: str = "LIVE", repair: bool | None = None) -> dict[str, Any]:
    """Compare open LIVE journal rows against Dhan's net positions."""
    from index_ai.learning import open_trades_for_mode

    out: dict[str, Any] = {
        "checked_at_ist": now_ist_iso(),
        "mode": mode,
        "issues": [],
        "repaired": [],
        "ok": True,
    }
    if str(mode).upper() != "LIVE":
        out["skipped"] = "reconciliation only applies to LIVE trades"
        return out

    trades = [t for t in open_trades_for_mode("LIVE")]
    if not trades:
        out["skipped"] = "no open live trades"
        return out

    try:
        from index_ai.dhan_orders import build_position_index

        actual = build_position_index(client)
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"could not read broker positions: {exc}"
        return out

    expected = expected_positions(trades)
    issues = diff_positions(expected, actual)
    out["issues"] = issues
    out["ok"] = not issues
    out["expected_ids"] = len(expected)
    out["broker_ids"] = len(actual)

    do_repair = auto_repair_enabled() if repair is None else repair
    if do_repair and issues:
        out["repaired"] = _repair_ghosts(trades, issues)
    return out


def _repair_ghosts(trades: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Close journal rows whose legs the broker no longer holds.

    Only GHOST_JOURNAL is auto-repaired: the position is provably gone at the
    broker, so leaving the row open corrupts every downstream P&L and risk count.
    Partial fills and orphans are reported for a human — repairing those means
    guessing at intent.
    """
    from index_ai.learning import update_trade_status

    ghosts = {i["security_id"] for i in issues if i["kind"] == GHOST_JOURNAL}
    if not ghosts:
        return []
    done: list[dict[str, Any]] = []
    for t in trades:
        sids = {int(x["security_id"]) for x in _journal_legs(t) if x.get("security_id") is not None}
        if not sids or not sids.issubset(ghosts):
            continue
        tid = str(t.get("id") or "")
        if not tid:
            continue
        try:
            update_trade_status(tid, "CLOSED_RECONCILED")
            done.append({"trade_id": tid, "instrument": t.get("instrument"), "security_ids": sorted(sids)})
        except Exception:
            continue
    return done


if __name__ == "__main__":  # ponytail self-check
    exp = {1: 65, 2: -30, 3: 20}
    act = {1: 65, 2: -15, 4: 10}          # 2 partially closed, 3 gone, 4 unknown
    kinds = {i["security_id"]: i["kind"] for i in diff_positions(exp, act)}
    assert 1 not in kinds                                  # matched
    assert kinds[2] == UNDER_FILLED, kinds
    assert kinds[3] == GHOST_JOURNAL, kinds
    assert kinds[4] == ORPHAN_BROKER, kinds
    assert diff_positions({5: 65}, {5: 130})[0]["kind"] == OVER_FILLED
    assert diff_positions({6: 65}, {6: -65})[0]["kind"] == ORPHAN_BROKER  # sign flip

    trades = [{"id": "t1", "option": {"legs": [
        {"security_id": 1, "quantity": 65, "transaction_type": "BUY"},
        {"security_id": 2, "quantity": 30, "transaction_type": "SELL"},
    ]}}]
    assert expected_positions(trades) == {1: 65, 2: -30}
    assert diff_positions(expected_positions(trades), {1: 65, 2: -30}) == []
    print("reconcile.py self-check ok")
