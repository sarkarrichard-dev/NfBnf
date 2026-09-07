"""One dataset from ``memory/crypto_journal.jsonl`` for the crypto model.

Pure data — no sklearn import here. Rows are chronological (by ``closed_at`` /
``exit_time``) so ``model.walk_forward`` can validate without leaking the future.
"""

from __future__ import annotations

import json
from typing import Any

from crypto.journal import JOURNAL_PATH
from crypto.ml.features import FEATURES, label, row_features


def _rows() -> list[dict[str, Any]]:
    if not JOURNAL_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def build_dataset() -> dict[str, Any]:
    xs: list[list[float]] = []
    ys: list[int] = []
    meta: list[dict[str, Any]] = []
    for r in _rows():
        y = label(r)
        if y is None:
            continue
        feats = row_features(r)
        if feats is None:
            continue
        xs.append([feats[k] for k in FEATURES])
        ys.append(y)
        meta.append({
            "when": str(r.get("closed_at") or r.get("exit_time") or r.get("day") or ""),
            "mode": r.get("mode", "paper"),
            "asset": r.get("asset", ""),
            "pnl_usd": float(r.get("pnl_usd") or 0.0),
        })

    order = sorted(range(len(meta)), key=lambda i: meta[i]["when"])
    live = sum(1 for m in meta if m["mode"] == "live")
    return {
        "feature_names": list(FEATURES),
        "X": [xs[i] for i in order],
        "y": [ys[i] for i in order],
        "meta": [meta[i] for i in order],
        "total_rows": len(ys),
        "live_rows": live,
    }


if __name__ == "__main__":  # self-check
    ds = build_dataset()
    assert set(ds) >= {"X", "y", "meta", "total_rows"}
    assert len(ds["X"]) == len(ds["y"]) == len(ds["meta"])
    assert all(len(row) == len(FEATURES) for row in ds["X"])
    whens = [m["when"] for m in ds["meta"]]
    assert whens == sorted(whens), "must be chronological for walk-forward"
    print(f"crypto.ml.dataset self-check ok — {ds['total_rows']} rows ({ds['live_rows']} live)")
