"""Crypto paper journal + lane state, in ``memory/`` alongside the index files.

- ``crypto_state.json`` — per (strategy, asset) open position + session counters.
- ``crypto_journal.jsonl`` — one row per closed trade.

Both use the same atomic-write and append pattern as the index paper lanes.
"""

from __future__ import annotations

import json
import os
from typing import Any

from crypto.config import CRYPTO_MEMORY

STATE_PATH = CRYPTO_MEMORY / "crypto_state.json"
JOURNAL_PATH = CRYPTO_MEMORY / "crypto_journal.jsonl"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def journal(trade: dict[str, Any]) -> None:
    CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade, default=str) + "\n")


def recent(limit: int = 100) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:]


def day_rows(day: str, *, strategy: str | None = None) -> list[dict[str, Any]]:
    out = [r for r in recent(500) if r.get("day") == day]
    if strategy:
        out = [r for r in out if r.get("strategy") == strategy]
    return out


if __name__ == "__main__":  # self-check (tmp files, no clobber of real journal)
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    STATE_PATH = d / "crypto_state.json"
    JOURNAL_PATH = d / "crypto_journal.jsonl"
    save_state({"ny_n_break:BTCUSD": {"position": None}})
    assert load_state()["ny_n_break:BTCUSD"]["position"] is None
    journal({"day": "2026-09-07", "strategy": "ny_n_break", "asset": "BTCUSD", "pnl_usd": 12.0})
    journal({"day": "2026-09-07", "strategy": "ichimoku", "asset": "ETHUSD", "pnl_usd": -3.0})
    assert len(recent()) == 2
    assert len(day_rows("2026-09-07", strategy="ny_n_break")) == 1
    print("crypto.journal self-check ok")
