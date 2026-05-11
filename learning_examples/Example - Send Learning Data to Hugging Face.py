"""Example: stream CSV rows from the `Files To Teach AI` folder.

Install optional Hugging Face helpers first:
  pip install -e ".[hf]"

Preview rows:
  python -m trading_ai_engine.ml.hf_cli --root "Files To Teach AI" --preview-rows 5
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trading_ai_engine.ml.hf_pack import build_streaming_csv_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview rows from a learning-data folder.")
    parser.add_argument("--root", type=Path, required=True, help="Folder containing CSV files.")
    parser.add_argument("--max-rows", type=int, default=1000, help="Stop after this many rows.")
    args = parser.parse_args()

    dataset = build_streaming_csv_dataset(args.root.expanduser().resolve(), chunksize=50_000)
    seen = 0
    for _row in dataset:
        seen += 1
        if seen >= args.max_rows:
            break
    print(f"Read {seen} rows. Add your model training step here.")


if __name__ == "__main__":
    main()
