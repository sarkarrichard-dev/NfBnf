"""Shared ``os.getenv`` parsers — was hand-rolled identically in config.py,
entry_guard.py, learning.py and strategy_params.py."""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    os.environ["_ENV_TEST_BOOL"] = "yes"
    os.environ["_ENV_TEST_INT"] = "not-a-number"
    assert env_bool("_ENV_TEST_BOOL", False) is True
    assert env_bool("_ENV_TEST_MISSING", True) is True
    assert env_int("_ENV_TEST_INT", 7) == 7
    assert env_float("_ENV_TEST_MISSING", 1.5) == 1.5
    print("index_ai.env self-check ok")
