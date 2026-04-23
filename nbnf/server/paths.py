from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "nbnf.sqlite"
