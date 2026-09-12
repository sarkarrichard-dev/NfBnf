"""Atomic JSON state writes — was hand-rolled identically in crypto/journal.py
and commodities/lanes.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` as JSON, atomically.

    ``os.replace`` is atomic but on Windows fails with WinError 5 when
    antivirus / a sync agent briefly holds the target open — retry, then
    fall back to a plain in-place write rather than lose state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, default=str)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 5:
                break
            time.sleep(0.25)
    path.write_text(payload, encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "state.json"
        atomic_write_json(p, {"a": 1})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    print("index_ai.atomic_io self-check ok")
