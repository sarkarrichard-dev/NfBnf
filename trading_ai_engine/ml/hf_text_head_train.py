"""
Train a small TF–IDF + logistic head on a **streaming** Hugging Face text dataset (online labels).

Requires: ``pip install -e ".[hf_train]"`` (adds scikit-learn). Produces ``hf_text_head.joblib`` under
``Your Trading Data/AI Models`` for optional future fusion (not wired into fusion by default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import islice
from pathlib import Path
from typing import Any

from trading_ai_engine.ml.hf_online_digest import parse_hub_dataset_spec
from trading_ai_engine.server.paths import DATA_DIR

MODEL_DIR = DATA_DIR / "AI Models"
OUT_PATH = MODEL_DIR / "hf_text_head.joblib"


def _load_rows(
    spec: str,
    *,
    text_col: str,
    label_col: str,
    max_rows: int,
) -> tuple[list[str], list[int]]:
    from datasets import load_dataset

    repo, config, split = parse_hub_dataset_spec(spec)
    if config:
        ds = load_dataset(repo, config, split=split, streaming=True, trust_remote_code=False)
    else:
        ds = load_dataset(repo, split=split, streaming=True, trust_remote_code=False)
    texts: list[str] = []
    labels: list[int] = []
    for row in islice(iter(ds), max_rows):
        if not isinstance(row, dict):
            continue
        t = row.get(text_col)
        y = row.get(label_col)
        if t is None or y is None:
            continue
        texts.append(str(t)[:8000])
        try:
            labels.append(int(y))
        except (TypeError, ValueError):
            continue
    return texts, labels


def train_and_save(
    *,
    dataset_spec: str,
    text_col: str,
    label_col: str,
    max_rows: int,
    out_path: Path | None = None,
) -> dict[str, Any]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        import joblib
    except ImportError as e:
        raise RuntimeError('Install: pip install -e ".[hf_train]"') from e

    path = out_path or OUT_PATH
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    texts, labels = _load_rows(dataset_spec, text_col=text_col, label_col=label_col, max_rows=max_rows)
    if len(texts) < 50 or len(set(labels)) < 2:
        return {
            "status": "not_enough_rows",
            "rows": len(texts),
            "unique_labels": len(set(labels)),
            "hint": "Increase --max-rows or pick a dataset with text + integer labels.",
        }

    vec = TfidfVectorizer(max_features=800, min_df=2, strip_accents="unicode")
    X = vec.fit_transform(texts)
    clf = LogisticRegression(max_iter=400, class_weight="balanced", random_state=42)
    clf.fit(X, labels)
    joblib.dump({"vectorizer": vec, "classifier": clf, "meta": {"text_col": text_col, "label_col": label_col, "spec": dataset_spec}}, path)
    train_acc = float(clf.score(X, labels))
    return {
        "status": "ok",
        "path": str(path),
        "rows": len(texts),
        "classes": sorted(set(labels)),
        "train_accuracy_in_sample": round(train_acc, 4),
        "warning": "In-sample accuracy only — validate on held-out data before any live use.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Train TF-IDF + logistic on a Hub text dataset (streaming cap).")
    p.add_argument(
        "--dataset-spec",
        default=os.environ.get("TRADING_AI_HF_LEARNING_DATASETS", "ag_news").split(",")[0].strip(),
        help="Single Hub spec (same format as TRADING_AI_HF_LEARNING_DATASETS first entry).",
    )
    p.add_argument("--text-col", default=os.environ.get("TRADING_AI_HF_TEXT_COL", "text"))
    p.add_argument("--label-col", default=os.environ.get("TRADING_AI_HF_LABEL_COL", "label"))
    p.add_argument("--max-rows", type=int, default=8000)
    args = p.parse_args()
    try:
        meta = train_and_save(
            dataset_spec=args.dataset_spec,
            text_col=args.text_col,
            label_col=args.label_col,
            max_rows=max(100, args.max_rows),
        )
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from e
    print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    main()
