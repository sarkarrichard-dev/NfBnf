"""
One dataset from every lane's journal.

Sources:
  * options-sell / options-buy  -> SQLite ``trades`` table (index_ai.learning)
  * directional futures         -> memory/futures_journal.jsonl
  * CPR options (buy + sell)    -> memory/options_cpr_journal.jsonl

Rows are normalised through ``brain.features.unified_features`` so the model sees
one vector regardless of origin. Backtest output can be folded in as *seed* data
(``include_backtest``) to get a model off the ground before enough forward trades
exist — flagged separately so it can be dropped once real trades accumulate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from index_ai.brain.features import FEATURES, label, unified_features
from index_ai.config import MEMORY_DIR

_JSONL_LANES: dict[str, str] = {
    "futures": "futures_journal.jsonl",
    "options_cpr": "options_cpr_journal.jsonl",
}
_BACKTEST_DIRS = ("research/futures", "research/options_cpr")


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _sqlite_trades() -> Iterator[dict[str, Any]]:
    try:
        from index_ai.learning import _row_to_trade, connect
    except Exception:
        return
    try:
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY created_at ASC"
            ).fetchall()
    except Exception:
        return
    for row in rows:
        try:
            yield _row_to_trade(row)
        except Exception:
            continue


def _backtest_trades() -> Iterator[dict[str, Any]]:
    for d in _BACKTEST_DIRS:
        root = Path(d)
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*.json")):
            if f.name in {"summary.json", "report.json"}:
                continue
            try:
                blob = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for t in blob.get("trades") or []:
                t.setdefault("lane", "futures" if "futures" in d else t.get("lane") or "sell")
                yield t


def iter_trades(*, include_backtest: bool = False) -> Iterator[tuple[dict[str, Any], str, bool]]:
    """Yield (trade, source, is_backtest) for every closed trade across all lanes."""
    for t in _sqlite_trades():
        yield t, "options_journal", False
    for lane, fname in _JSONL_LANES.items():
        for t in _read_jsonl(MEMORY_DIR / fname):
            t.setdefault("lane", "futures" if lane == "futures" else t.get("lane") or "sell")
            yield t, lane, False
    if include_backtest:
        for t in _backtest_trades():
            yield t, "backtest", True


def build_dataset(*, include_backtest: bool = False) -> dict[str, Any]:
    """X / y / metadata for training. Pure data — no sklearn import here."""
    xs: list[list[float]] = []
    ys: list[int] = []
    meta: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    for trade, source, is_bt in iter_trades(include_backtest=include_backtest):
        y = label(trade)
        if y is None:
            continue
        feats = unified_features(trade)
        if feats is None:
            continue
        xs.append([feats[k] for k in FEATURES])
        ys.append(y)
        meta.append({
            "source": source,
            "is_backtest": is_bt,
            "instrument": str(trade.get("instrument") or ""),
            "when": str(trade.get("exit_time") or trade.get("session") or trade.get("closed_at") or ""),
            "net_rupees": trade.get("net_rupees") if trade.get("net_rupees") is not None else trade.get("pnl"),
        })
        by_source[source] = by_source.get(source, 0) + 1

    order = sorted(range(len(meta)), key=lambda i: meta[i]["when"])  # chronological for walk-forward
    return {
        "feature_names": list(FEATURES),
        "X": [xs[i] for i in order],
        "y": [ys[i] for i in order],
        "meta": [meta[i] for i in order],
        "counts": by_source,
        "live_rows": sum(1 for m in meta if not m["is_backtest"]),
        "total_rows": len(ys),
    }


if __name__ == "__main__":  # ponytail self-check
    ds = build_dataset()
    assert set(ds) >= {"X", "y", "meta", "counts", "feature_names"}
    assert len(ds["X"]) == len(ds["y"]) == len(ds["meta"])
    assert all(len(row) == len(FEATURES) for row in ds["X"])
    whens = [m["when"] for m in ds["meta"]]
    assert whens == sorted(whens), "dataset must be chronological for walk-forward"
    print(f"store.py self-check ok — {ds['total_rows']} rows ({ds['live_rows']} live) {ds['counts']}")
