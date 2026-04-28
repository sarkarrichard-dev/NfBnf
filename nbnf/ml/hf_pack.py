from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

"""Hugging Face ``datasets`` helpers for **full** tabular reads (streaming).

QuantTape's SQLite ``ml_datasets`` ingest is intentionally a **catalog**: per-file
column stats + small head + row count cap — not a copy of every row. That keeps the
desk fast and the DB small.

Use this module when you want **every row** (e.g. training) via an ``IterableDataset``
that reads CSVs in chunks without holding the whole corpus in RAM.
"""


def collect_csv_paths(root: Path) -> list[Path]:
    """All ``*.csv`` under ``root`` (recursive), excluding Excel-style lock files."""
    root = root.resolve()
    out: list[Path] = []
    for p in sorted(root.rglob("*.csv")):
        if not p.is_file():
            continue
        if p.name.startswith("~$"):
            continue
        out.append(p)
    return out


def iter_csv_batches(
    paths: list[Path],
    *,
    root: Path,
    chunksize: int,
    on_skip: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Yield Hugging-Face-style **batches**: each value is a list of length ``<= chunksize``.

    Adds ``_source_file`` (relative path under ``root``) to every row.
    Skips unreadable files after calling ``on_skip(path, exc)`` if provided.
    """
    root = root.resolve()
    for path in paths:
        rel = str(path.resolve().relative_to(root)).replace("\\", "/")
        try:
            for chunk in pd.read_csv(
                path,
                chunksize=chunksize,
                low_memory=False,
                encoding_errors="replace",
            ):
                if chunk.empty:
                    continue
                batch: dict[str, Any] = chunk.to_dict(orient="list")
                n = len(chunk)
                batch["_source_file"] = [rel] * n
                yield batch
        except Exception as e:
            if on_skip is not None:
                on_skip(path, e)
            continue


def build_streaming_csv_dataset(
    root: Path,
    *,
    chunksize: int = 50_000,
    on_skip: Any | None = None,
):
    """
    Build an ``IterableDataset`` over all CSVs under ``root`` (one **row** per yield).

    Requires: ``pip install -e ".[hf]"``

    Reads each file in ``chunksize`` row blocks with pandas, then yields rows so RAM
    stays bounded. All CSVs should share the **same columns** (plus ``_source_file``);
    mixed schemas can fail mid-stream.
    """
    from datasets import IterableDataset

    root = root.resolve()
    paths = collect_csv_paths(root)
    if not paths:
        raise FileNotFoundError(f"No CSV files under {root}")

    def _row_gen() -> Iterator[dict[str, Any]]:
        for batch in iter_csv_batches(paths, root=root, chunksize=chunksize, on_skip=on_skip):
            keys = list(batch.keys())
            if not keys:
                continue
            n = len(batch[keys[0]])
            for i in range(n):
                yield {k: batch[k][i] for k in keys}

    return IterableDataset.from_generator(_row_gen)
