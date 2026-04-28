from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from nbnf.ml.hf_pack import build_streaming_csv_dataset, collect_csv_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stream **all rows** from every CSV under a folder tree as a Hugging Face "
            "IterableDataset (chunked reads — suitable for large corpora). "
            "This is separate from QuantTape's SQLite catalog ingest."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory to scan recursively for *.csv",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=50_000,
        metavar="N",
        help="Pandas read_csv chunksize per file (default 50000).",
    )
    parser.add_argument(
        "--list-csv-count",
        action="store_true",
        help="Only count CSV paths and exit (no datasets import).",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=0,
        metavar="N",
        help="Print first N rows (as JSON objects) then exit.",
    )
    parser.add_argument(
        "--push-to-hub",
        metavar="REPO_ID",
        help="Upload streaming dataset to the Hub (set HF_TOKEN in env).",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="With --push-to-hub, create a private dataset.",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    if args.list_csv_count:
        paths = collect_csv_paths(root)
        print(json.dumps({"root": str(root), "csv_files": len(paths)}, indent=2))
        return

    skipped: list[dict[str, str]] = []

    def on_skip(path: Path, exc: BaseException) -> None:
        skipped.append({"path": str(path), "error": str(exc)})

    try:
        ds = build_streaming_csv_dataset(root, chunksize=args.chunksize, on_skip=on_skip)
    except ImportError as e:
        print(
            "Missing dependency. Install with:\n  pip install -e \".[hf]\"",
            file=sys.stderr,
        )
        raise SystemExit(1) from e
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2) from e

    if args.preview_rows > 0:
        summaries: list[dict[str, Any]] = []
        for i, row in enumerate(ds):
            if i >= args.preview_rows:
                break
            summaries.append(row)
        print(
            json.dumps(
                {"preview_rows": summaries, "skipped_files": skipped[:50], "skipped_n": len(skipped)},
                indent=2,
                default=str,
            )
        )
        return

    if args.push_to_hub:
        ds.push_to_hub(args.push_to_hub, private=args.private)
        print(json.dumps({"pushed_to": args.push_to_hub, "skipped_n": len(skipped)}, indent=2))
        return

    print(
        json.dumps(
            {
                "message": "Dataset built in memory as IterableDataset (streaming).",
                "hint": "Use --preview-rows 3 to inspect, or --push-to-hub user/name to upload.",
                "root": str(root),
                "csv_files": len(collect_csv_paths(root)),
                "skipped_n": len(skipped),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
