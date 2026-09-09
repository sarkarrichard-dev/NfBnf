"""Re-measure per-index / per-lane gross-per-trade from the real SQLite journal.

``options_cpr/viability.py::OBSERVED_GROSS_PER_TRADE`` was a fixed 2026-08-29
backtest constant. ``entry_guard._viable_sell_blocks`` now gates the LIVE
credit-sell lane on ``viability()``'s verdict, so that number needs to track
what the lane actually does. This script measures it from closed trades in
``memory/trade_memory.sqlite`` (real fills, mid-to-mid for paper) and prints a
comparison table. It does **not** edit ``viability.py`` — updating the constant
changes which indices trade, so that stays a human decision.

    python -m scripts.measure_viability_gross            # all lanes
    python -m scripts.measure_viability_gross --paper    # exclude LIVE rows
    python -m scripts.measure_viability_gross --min 30   # hide thin cells

Journal ``pnl`` is GROSS — ``credit_spread.spread_pnl_rupees`` /
``exit.estimate_pnl_rupees`` never subtract charges — so mean(pnl) over a lane's
trades *is* its gross-per-trade. Rows whose PnL was inferred from an index move
(option LTP unavailable at close) are dropped as unreliable.
"""

from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from typing import Any

from index_ai.learning import _row_to_trade, connect
from index_ai.strategies.options_cpr.viability import OBSERVED_GROSS_PER_TRADE
from index_ai.strategies.strategy_router import trade_lane

_MIN_DEFAULT = 30


def _is_estimated(trade: dict[str, Any]) -> bool:
    o = trade.get("option") or {}
    return bool(o.get("exit_inferred_from_pnl")) or "estimated from index move" in str(
        trade.get("note") or ""
    )


def _is_live(trade: dict[str, Any]) -> bool:
    return str(trade.get("mode") or "").upper() == "LIVE" or str(
        trade.get("status") or ""
    ).upper().startswith("LIVE")


def measure(*, paper_only: bool = False) -> dict[tuple[str, str], dict[str, Any]]:
    """{(instrument, lane): {n, n_clean, mean, median, win_pct, first, last}} — clean
    = PnL not inferred from an index move (and paper-only when requested)."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with connect() as db:
        for row in db.execute(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY created_at"
        ).fetchall():
            t = _row_to_trade(row)
            lane = trade_lane(t.get("action") or "")
            if lane == "none":
                continue
            buckets[(str(t["instrument"]).upper(), lane)].append(t)

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key, trades in sorted(buckets.items()):
        clean = [
            t
            for t in trades
            if not _is_estimated(t) and not (paper_only and _is_live(t))
        ]
        pnls = [float(t["pnl"]) for t in clean]
        if not pnls:
            continue
        out[key] = {
            "n": len(trades),
            "n_clean": len(pnls),
            "mean": round(st.mean(pnls), 1),
            "median": round(st.median(pnls), 1),
            "win_pct": round(100 * sum(1 for p in pnls if p > 0) / len(pnls)),
            "first": clean[0]["created_at"][:10],
            "last": clean[-1]["created_at"][:10],
        }
    return out


def _verdict_preview(instrument: str, lane: str, gross: float) -> str:
    from index_ai.strategies.options_cpr.viability import viability

    v = viability(instrument, lane, gross_per_trade=gross)
    return f"{v.verdict} ({v.reason})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true", help="exclude LIVE rows")
    ap.add_argument("--min", type=int, default=_MIN_DEFAULT, help="min clean trades to trust a cell")
    args = ap.parse_args()

    stats = measure(paper_only=args.paper)
    print(
        f"{'bucket':20} {'n':>4} {'clean':>6} {'mean Rs':>9} {'median Rs':>10} "
        f"{'win%':>5} {'constant':>9}  span / note"
    )
    print("-" * 100)
    for (inst, lane), s in stats.items():
        const = OBSERVED_GROSS_PER_TRADE.get((inst, lane))
        thin = s["n_clean"] < args.min
        note = f"{s['first']}..{s['last']}"
        if thin:
            note += f"  THIN (<{args.min}) — keep the constant"
        print(
            f"{inst + '/' + lane:20} {s['n']:>4} {s['n_clean']:>6} {s['mean']:>8.0f} "
            f"{s['median']:>9.0f} {s['win_pct']:>5} "
            f"{('—' if const is None else f'{const:.0f}'):>9}  {note}"
        )
        if lane == "sell" and not thin:
            print(f"{'':20} → if updated: {_verdict_preview(inst, lane, s['mean'])}")
    print(
        "\nJournal pnl is gross (no charges). Updating OBSERVED_GROSS_PER_TRADE in "
        "viability.py changes which indices the sell lane trades — decide, don't auto-apply."
    )


def _selfcheck() -> None:
    assert _is_estimated({"note": "closed — PnL estimated from index move"})
    assert _is_estimated({"option": {"exit_inferred_from_pnl": True}})
    assert not _is_estimated({"option": {}, "note": "clean close"})
    assert _is_live({"mode": "LIVE"}) and _is_live({"status": "LIVE_TRADED"})
    assert not _is_live({"mode": "PAPER", "status": "CLOSED"})
    s = measure()  # runs against whatever journal exists; shape only
    for v in s.values():
        assert {"n", "n_clean", "mean", "median", "win_pct"} <= v.keys()
    print("measure_viability_gross.py self-check ok")


if __name__ == "__main__":
    import sys

    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        main()
