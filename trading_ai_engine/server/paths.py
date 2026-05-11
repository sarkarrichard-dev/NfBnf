from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO_ROOT / "dashboard"
DATA_DIR = REPO_ROOT / "Your Trading Data"
DB_PATH = DATA_DIR / "Trading AI Memory.sqlite"


def ml_data_roots() -> list[Path]:
    """
    Directories scanned for tabular ML ingest: ``Your Trading Data/``, optional
    ``Files To Teach AI/`` under the repo, plus ``TRADING_AI_EXTRA_DATA_FOLDERS``.
    """
    roots: list[Path] = [DATA_DIR.resolve()]
    legacy = (REPO_ROOT / "Files To Teach AI").resolve()
    if legacy.is_dir():
        roots.append(legacy)
    extra = os.environ.get("TRADING_AI_EXTRA_DATA_FOLDERS", "").strip()
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
