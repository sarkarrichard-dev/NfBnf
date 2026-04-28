from __future__ import annotations

import argparse
import json
from pathlib import Path

from nbnf.ml.ingest import default_cli_progress, scan_and_ingest
from nbnf.server.paths import ml_data_roots


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively scan every subfolder, ingest each tabular file (CSV/TSV/Parquet/Excel) "
            "into QuantTape SQLite (ml_datasets) for ML + AI digest. "
            "Large .xlsx files use streaming (first sheet, row cap) — not fully loaded into RAM."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=str,
        help=(
            "Root directories to scan exclusively (every file under each tree). "
            "If omitted, uses built-in roots: data/, data for ml/, and NBNF_ML_EXTRA_DIRS."
        ),
    )
    parser.add_argument(
        "--also-builtins",
        action="store_true",
        help="Scan built-in ml_data_roots() in addition to PATH arguments.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=100_000,
        metavar="N",
        help="Maximum rows to read per file for profiling (default 100000).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-file progress on stderr.",
    )
    args = parser.parse_args()

    if args.paths:
        extra = [Path(p).expanduser().resolve() for p in args.paths]
        roots = (ml_data_roots() + extra) if args.also_builtins else extra
    else:
        if args.also_builtins:
            parser.error("--also-builtins requires at least one PATH")
        roots = None

    if roots is not None:
        seen: set[str] = set()
        uniq: list[Path] = []
        for r in roots:
            k = str(r.resolve())
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        roots = uniq

    on_file = None if args.quiet else default_cli_progress
    summary = scan_and_ingest(roots, max_rows_per_file=args.max_rows, on_file=on_file)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
