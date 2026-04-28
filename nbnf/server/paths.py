from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "nbnf.sqlite"


def ml_data_roots() -> list[Path]:
    """
    Directories scanned for tabular ML ingest: ``data/``, optional ``data for ml/`` under the repo,
    plus ``NBNF_ML_EXTRA_DIRS`` (semicolon- or pipe-separated absolute paths).
    """
    roots: list[Path] = [DATA_DIR.resolve()]
    legacy = (REPO_ROOT / "data for ml").resolve()
    if legacy.is_dir():
        roots.append(legacy)
    extra = os.environ.get("NBNF_ML_EXTRA_DIRS", "").strip()
    if extra:
        for part in extra.replace("|", ";").split(";"):
            p = Path(part.strip())
            if p.is_dir():
                roots.append(p.resolve())
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
