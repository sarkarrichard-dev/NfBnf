#!/usr/bin/env python
"""PostToolUse hook: tidy Python edits, flag stale/broken dashboard builds.

- *.py under index_ai/, crypto/, commodities/, tests/, scripts/
                                  -> `ruff check --fix` + `ruff format` on that file
- dashboard/src/**               -> `tsc --noEmit` and remind that dist/ is stale

Reads the hook payload on stdin, does its work, and stays silent unless there's
something the user should see. Never blocks the edit.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RUFF = "ruff"  # on PATH; falls back cleanly if missing


def _target(payload: dict) -> Path | None:
    ti = payload.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("path")
    return Path(p) if p else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    path = _target(payload)
    if path is None or not path.exists():
        return 0

    rel = path.as_posix()
    py_dirs = ("/index_ai/", "/crypto/", "/commodities/", "/tests/")

    if path.suffix == ".py" and (any(d in f"/{rel}" for d in py_dirs) or rel.startswith("scripts/")):
        try:
            subprocess.run([RUFF, "check", "--fix", "--quiet", str(path)], timeout=30,
                           capture_output=True)
            subprocess.run([RUFF, "format", "--quiet", str(path)], timeout=30,
                           capture_output=True)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # surface anything ruff could NOT auto-fix
        try:
            r = subprocess.run([RUFF, "check", "--quiet", str(path)], timeout=30,
                               capture_output=True, text=True)
            if r.stdout.strip():
                print(f"ruff (unfixed) in {path.name}:\n{r.stdout.strip()}", file=sys.stderr)
                return 2
        except Exception:
            pass
        return 0

    if "dashboard/src/" in f"/{rel}":
        msgs = ["dashboard/src changed — run `npm --prefix dashboard run build` "
                "before serving, or the UI shows a stale bundle."]
        try:
            r = subprocess.run(
                ["npx", "tsc", "-b", "--noEmit"], cwd="dashboard",
                timeout=60, capture_output=True, text=True, shell=(sys.platform == "win32"),
            )
            if r.returncode != 0:
                out = (r.stdout or r.stderr or "").strip()
                msgs.append(f"tsc found type errors:\n{out[:2000]}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        print("\n".join(msgs), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
