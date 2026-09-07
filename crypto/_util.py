"""Tiny shared coercion helpers. Stdlib-only — safe to import from anywhere in
``crypto`` (including ``config``) with no cycle risk."""

from __future__ import annotations

import os
from typing import Any


def num(v: Any, default: float = 0.0) -> float:
    """Any value → float. Unparseable input and NaN fall back to ``default``."""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def env_float(name: str, default: float) -> float:
    """Env var → float, ``default`` when unset or unparseable."""
    return num(os.getenv(name), default)


if __name__ == "__main__":  # self-check
    assert num("3.5") == 3.5
    assert num(None) == 0.0 and num("x", -1.0) == -1.0
    assert num(float("nan"), 7.0) == 7.0
    os.environ["_UTIL_T"] = "2.5"
    assert env_float("_UTIL_T", 0.0) == 2.5 and env_float("_UTIL_MISSING", 9.0) == 9.0
    print("crypto._util self-check ok")
