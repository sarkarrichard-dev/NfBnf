"""Resolve secrets from environment first, then optional ``local_secrets`` (gitignored)."""

from __future__ import annotations

import os
from types import ModuleType

from dotenv import load_dotenv

load_dotenv()

try:
    from trading_ai_engine import local_secrets as _local_secrets
except ImportError:
    _local_secrets: ModuleType | None = None

# Names we may copy from ``local_secrets`` into ``os.environ`` when the env var is unset/empty
# (so libraries that only read the environment, e.g. ``huggingface_hub``, still see tokens).
_LOCAL_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CHAT_MODEL",
    "DHAN_CLIENT_ID",
    "DHAN_ACCESS_TOKEN",
    "DHAN_FEED_URL",
    "DHAN_API_BASE_URL",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "TRADING_AI_DHAN_LTP_MAP",
    "OPENALGO_BASE_URL",
    "OPENALGO_API_KEY",
    "OPENALGO_STRATEGY",
)


def apply_local_secrets_into_environ() -> None:
    """If ``local_secrets.py`` exists, copy defined keys into ``os.environ`` only when not already set."""
    if _local_secrets is None:
        return
    for name in _LOCAL_ENV_KEYS:
        cur = os.environ.get(name)
        if cur is not None and str(cur).strip() != "":
            continue
        alt = getattr(_local_secrets, name, None)
        if alt is not None and str(alt).strip() != "":
            os.environ[name] = str(alt).strip()


def env_or_local(name: str, default: str | None = None) -> str | None:
    """
    Return a non-empty value from ``os.environ``, else from ``trading_ai_engine.local_secrets``
    if that module exists and defines ``name``, else ``default``.

    Copy ``local_secrets.example.py`` → ``local_secrets.py`` and fill in values you want outside ``.env``.
    After import, ``apply_local_secrets_into_environ`` has merged those into ``os.environ`` when unset,
    so this usually reads from the environment.
    """
    raw = os.environ.get(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    if _local_secrets is not None:
        alt = getattr(_local_secrets, name, None)
        if alt is not None and str(alt).strip() != "":
            return str(alt).strip()
    return default


apply_local_secrets_into_environ()
