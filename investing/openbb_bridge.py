"""Calls OpenBB from its own isolated venv (``.venv-openbb``) via subprocess.

OpenBB's own dependency tree collides with pinned fastapi/uvicorn/aiohttp
versions the live trading server needs — measured 2026-09-20: installing
``openbb`` into the main environment silently changed all three. Never add
``openbb`` to ``pyproject.toml``; keep it walled off in ``.venv-openbb`` and
only ever talk to it through this subprocess boundary.

Setup (one-time, not committed — .venv-openbb is gitignored):
    python -m venv .venv-openbb
    .venv-openbb/Scripts/python.exe -m pip install openbb
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv-openbb" / "Scripts" / "python.exe"
_WORKER = Path(__file__).resolve().parent / "_openbb_worker.py"


def fetch_fundamentals(symbols: list[str], *, timeout: float = 600.0) -> dict[str, dict]:
    """``{symbol: {...fundamentals...} | {"error": "..."}}`` for each requested symbol."""
    if not _VENV_PYTHON.is_file():
        raise RuntimeError(
            f"OpenBB venv missing at {_VENV_PYTHON} — run:\n"
            f"  python -m venv .venv-openbb\n"
            f"  .venv-openbb/Scripts/python.exe -m pip install openbb"
        )
    proc = subprocess.run(
        [str(_VENV_PYTHON), str(_WORKER)],
        input=json.dumps(list(symbols)),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"openbb worker failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)
