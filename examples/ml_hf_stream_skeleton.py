"""
Skeleton: stream all CSV rows under a folder as a Hugging Face IterableDataset.

Install once (from repo root):
  pip install -e ".[hf]"

Preview from CLI:
  python -m nbnf.ml.hf_cli --root "…/data for ml" --preview-rows 5

Use this script as a starting point for training loops (add your model / loss).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nbnf.ml.hf_pack import build_streaming_csv_dataset


def main() -> None:
    p = argparse.ArgumentParser(description="Iterate HF streaming CSV dataset (training skeleton).")
    p.add_argument("--root", type=Path, required=True, help="Root folder containing *.csv (recursive).")
    p.add_argument("--batch-size", type=int, default=256, help="Rows per training micro-batch.")
    p.add_argument("--max-rows", type=int, default=10_000, help="Stop after this many rows (demo cap).")
    args = p.parse_args()

    ds = build_streaming_csv_dataset(args.root.expanduser().resolve(), chunksize=50_000)
    batch: list[dict] = []
    seen = 0
    for row in ds:
        batch.append(row)
        seen += 1
        if len(batch) >= args.batch_size:
            # --- replace with model step, e.g. forward(features_tensor(batch)) ---
            _ = len(batch)
            batch.clear()
        if seen >= args.max_rows:
            break
    if batch:
        _ = len(batch)
    print(f"Streamed {seen} rows (cap {args.max_rows}). Plug in your model where marked.")


if __name__ == "__main__":
    main()
