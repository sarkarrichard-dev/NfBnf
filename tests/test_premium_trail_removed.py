"""EXIT-02: the old percent-of-option-price trail is gone for good, and the rupee
profit trail (D-05) that used to sit behind it is still reached.

``.claude/worktrees`` (a stale detached worktree copy) and ``graphify-out``
(a generated graph) are deliberately outside the scan below.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from index_ai.config import settings
from index_ai.instruments import get_instrument
from index_ai.trailing import evaluate_open_trade, init_trail_meta

ROOT = Path(__file__).resolve().parents[1]
OLD_NAME = "premium_trail"


def test_module_is_gone() -> None:
    assert importlib.util.find_spec(f"index_ai.{OLD_NAME}") is None


def test_no_source_file_still_names_it() -> None:
    this_file = Path(__file__).resolve()
    hits = []
    for folder in ("index_ai", "tests", "dashboard/src"):
        for pattern in ("*.py", "*.ts", "*.tsx"):
            for path in (ROOT / folder).rglob(pattern):
                if "__pycache__" in path.parts or path.resolve() == this_file:
                    continue
                if OLD_NAME in path.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_rupee_profit_trail_is_still_called_for_a_buy_with_an_mtm_figure(monkeypatch) -> None:
    entry = 23400.0
    meta = init_trail_meta(
        entry_index_price=entry,
        action="BUY_CALL",
        transaction_type="BUY",
        instrument=get_instrument("NIFTY"),
    )
    trade = {
        "id": "t",
        "instrument": "NIFTY",
        "action": "BUY_CALL",
        "signal": {"action": "BUY_CALL", "price": entry},
        "option": {
            "transaction_type": "BUY",
            "ltp": 150.0,
            "last_option_ltp": 150.0,
            "mtm_pnl": 500.0,
            "trail_meta": meta,
        },
    }
    seen: list[float] = []

    def spy(m, mtm):
        seen.append(mtm)
        return m, True, "Profit trail: test"

    monkeypatch.setattr("index_ai.profit_trail.evaluate_profit_trail", spy)
    ev = evaluate_open_trade(trade, entry, settings().risk)

    assert seen == [500.0]
    assert ev["should_exit"] is True
    assert ev["profit_trail_exit"] is True
    assert ev["exit_reason"] == "Profit trail: test"
