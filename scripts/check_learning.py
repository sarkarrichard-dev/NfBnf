"""Quick audit: is learning active and wired to execution gates?"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from index_ai.executor import build_execution_plan
from index_ai.config import settings
from index_ai.instruments import get_instrument
from index_ai.learning import learned_settings, learning_report, update_learning
from index_ai.strategies.strategy import StrategySignal


def main() -> None:
    db = Path("memory/trade_memory.sqlite")
    print("DB exists:", db.exists())
    if db.exists():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        print("trades total:", conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
        print("trades open:", conn.execute("SELECT COUNT(*) FROM trades WHERE pnl IS NULL").fetchone()[0])
        print("trades closed:", conn.execute("SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL").fetchone()[0])
        print("feedback rows:", conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
        print(
            "real-trade-id feedback:",
            conn.execute("SELECT COUNT(*) FROM feedback WHERE trade_id = 'real-trade-id'").fetchone()[0],
        )
        print("\nLast 5 closed:")
        for r in conn.execute(
            "SELECT id, instrument, pnl, substr(created_at,1,19) AS t FROM trades "
            "WHERE pnl IS NOT NULL ORDER BY created_at DESC LIMIT 5"
        ):
            print(f"  {r['t']} {r['instrument']} pnl={r['pnl']}")

    learned = update_learning()
    print("\n=== Learning state (recomputed) ===")
    print(json.dumps(learned, indent=2))

  # Gate wiring check
    cfg = settings()
    inst = get_instrument("NIFTY")
    signal = StrategySignal(
        action="BUY_CALL",
        reason="audit",
        confidence=0.56,
        price=24000.0,
        pivot=0,
        bc=0,
        tc=0,
        ema_fast=1,
        ema_slow=0,
    )
    plan_low = build_execution_plan(
        app_settings=cfg,
        instrument=inst,
        signal=signal,
        option={"instrument": "NIFTY", "security_id": 1, "segment": "NSE_FNO", "quantity": 65, "transaction_type": "BUY"},
    )
    signal_high = StrategySignal(**{**signal.to_dict(), "confidence": 0.80})
    plan_high = build_execution_plan(
        app_settings=cfg,
        instrument=inst,
        signal=signal_high,
        option={"instrument": "NIFTY", "security_id": 1, "segment": "NSE_FNO", "quantity": 65, "transaction_type": "BUY"},
    )
    eff = learned["effective_min_confidence"]
    print(f"\n=== Gate wiring (effective min = {eff:.0%}) ===")
    print(f"  confidence 56% -> allowed={plan_low.allowed} ({plan_low.reason})")
    print(f"  confidence 80% -> allowed={plan_high.allowed} ({plan_high.reason})")

    report = learning_report()
    print(f"\nRecent feedback shown in UI: {len(report.get('recent_feedback') or [])}")


if __name__ == "__main__":
    main()
